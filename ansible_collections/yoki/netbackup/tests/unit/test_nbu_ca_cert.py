"""nbu_ca_cert: written only when checked, then pinned like an SSH host key."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from ansible_collections.yoki.netbackup.plugins.modules import nbu_ca_cert

URL = "https://nbu.test:1556/netbackup"


def make_ca(name: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


WEB, NBCA, OTHER = make_ca("web root"), make_ca("NBCA"), make_ca("someone else")


@pytest.fixture
def server(monkeypatch):
    """A primary whose /security/cacert answers with `served`; TLS checks pass."""
    state = {"served": [WEB, NBCA], "tls_problem": None, "versions": []}
    monkeypatch.setattr(nbu_ca_cert, "tls_check", lambda url, bundle, timeout: state["tls_problem"])

    def answer(request):
        version = request.headers["Accept"].split("version=")[-1]
        state["versions"].append(version)
        if version == "13.0":
            return httpx.Response(406)  # an older primary
        web, nbca = state["served"]
        return httpx.Response(
            200,
            json={
                "webRootCertificateData": web,
                "trustVersion": "x",
                "nbcaCertificateData": [{"cert": nbca}],
            },
        )

    with respx.mock(base_url=URL) as router:
        router.get("/security/cacert").mock(side_effect=answer)
        yield state


@pytest.fixture
def fp():
    return lambda pem: nbu_ca_cert.describe(nbu_ca_cert.normalise(pem))["sha256"]


def call(module, dest, **args):
    return module("nbu_ca_cert", dest=str(dest), **{"api_key": None, **args})


def test_first_contact_lists_fingerprints_and_writes_nothing(server, module, tmp_path, fp):
    dest = tmp_path / "ca.pem"
    result = call(module, dest)
    assert result["failed"] and "First contact" in result["msg"]
    assert [c["sha256"] for c in result["certificates"]] == [fp(WEB), fp(NBCA)]
    assert result["certificates"][0]["subject"] == "CN=web root"
    assert not dest.exists()
    assert server["versions"][:2] == ["13.0", "12.0"]  # stepped down on 406


def test_a_matching_fingerprint_writes_both_cas(server, module, tmp_path, fp):
    dest = tmp_path / "sub" / "ca.pem"
    result = call(module, dest, fingerprint=[fp(NBCA).lower().replace(":", "")])
    assert result["changed"] and result["verified"]
    assert dest.read_text().count("BEGIN CERTIFICATE") == 2
    assert oct(dest.stat().st_mode & 0o777) == "0o644"


def test_accept_new_trusts_on_first_use(server, module, tmp_path):
    assert call(module, tmp_path / "ca.pem", accept_new=True)["changed"]


def test_a_wrong_fingerprint_is_refused(server, module, tmp_path, fp):
    result = call(module, tmp_path / "ca.pem", fingerprint=[fp(OTHER)])
    assert result["failed"] and "does not match" in result["msg"]
    assert not (tmp_path / "ca.pem").exists()


def test_once_pinned_the_same_ca_is_ok_without_a_fingerprint(server, module, tmp_path):
    dest = tmp_path / "ca.pem"
    call(module, dest, accept_new=True)
    result = call(module, dest)
    assert result["changed"] is False and not result.get("failed")


def test_a_changed_ca_is_refused_unless_its_new_fingerprint_is_given(server, module, tmp_path, fp):
    dest = tmp_path / "ca.pem"
    call(module, dest, accept_new=True)
    server["served"] = [OTHER, NBCA]
    result = call(module, dest, accept_new=True)  # accept_new does not override a pin
    assert result["failed"] and "not the one pinned" in result["msg"]
    renewed = call(module, dest, fingerprint=[fp(OTHER)])
    assert renewed["changed"] and "someone else" not in dest.read_text()  # PEM, not names
    assert nbu_ca_cert.pems_in(dest.read_text())[0] == nbu_ca_cert.normalise(OTHER)


def test_certificates_that_do_not_verify_the_server_are_not_written(server, module, tmp_path):
    server["tls_problem"] = "hostname 'nbu.test' doesn't match 'nbu.test.example.com'"
    result = call(module, tmp_path / "ca.pem", accept_new=True)
    assert result["failed"] and "host name" in result["msg"]
    assert not (tmp_path / "ca.pem").exists()


def test_check_mode_writes_nothing(server, module, tmp_path):
    result = call(module, tmp_path / "ca.pem", accept_new=True, check=True)
    assert result["changed"] and not (tmp_path / "ca.pem").exists()


def test_pems_are_found_in_any_shape():
    flat = WEB.replace("\n", "")  # one line, as some JSON carries it
    body = json.loads(json.dumps({"a": [{"b": WEB}], "c": "x" + WEB + WEB, "d": None}))
    assert nbu_ca_cert.pems_in(body) == [nbu_ca_cert.normalise(WEB)]
    assert nbu_ca_cert.pems_in(
        flat.replace("-----BEGIN CERTIFICATE-----", "-----BEGIN CERTIFICATE-----\n")
    )


# --- the TLS check, against a real local HTTPS server --------------------------------


def _issue(ca_name: str, host: str):
    """A CA, and a server certificate it signed for `host` only."""
    import ipaddress  # noqa: F401 - names only, no IP SAN on purpose

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_name)])
    now = dt.datetime.now(dt.UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM).decode()  # noqa: E731
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem(ca), pem(cert), key_pem


@pytest.fixture
def https_server(tmp_path):
    import http.server
    import ssl
    import threading

    ca, cert, key = _issue("test CA", "localhost")
    (tmp_path / "cert.pem").write_text(cert)
    (tmp_path / "key.pem").write_text(key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")

    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield ca, httpd.server_address[1]
    httpd.shutdown()


def test_tls_check_accepts_the_right_ca_and_refuses_the_rest(https_server):
    ca, port = https_server
    good = f"https://localhost:{port}/netbackup"
    assert nbu_ca_cert.tls_check(good, ca, 5) is None
    assert nbu_ca_cert.tls_check(good, OTHER, 5) is not None  # another CA
    wrong_name = nbu_ca_cert.tls_check(f"https://127.0.0.1:{port}/netbackup", ca, 5)
    assert wrong_name is not None and ("match" in wrong_name or "IP" in wrong_name)
