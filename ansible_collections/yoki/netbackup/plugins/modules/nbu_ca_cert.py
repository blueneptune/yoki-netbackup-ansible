#!/usr/bin/python
"""yoki.netbackup.nbu_ca_cert: NetBackup's CA certificate, downloaded and pinned."""

from __future__ import annotations

DOCUMENTATION = r"""
module: nbu_ca_cert
short_description: Download a primary server's CA certificate, checked and pinned like an SSH host key
description:
  - Reads C(GET /security/cacert), which needs no login, and writes the CA certificates in the
    answer (C(webRootCertificateData), C(nbcaCertificateData)) to I(dest) as PEM. That file is
    then the I(ca_bundle) the other modules verify the server against.
  - The download itself cannot be verified, since the CA is what is being fetched. So the
    first time, the certificate is written only if one of its fingerprints matches
    I(fingerprint) (read on the primary with C(nbcertcmd -displayCACertDetail)), or if
    I(accept_new=true) says to trust it as it is. Otherwise the module fails and lists the
    fingerprints to check.
  - Once I(dest) exists it is the pin. The same CA again is C(ok); a different one is an error
    (a changed CA, or someone in between) unless I(fingerprint) matches the new one, which is
    how a CA renewal is accepted.
  - Before writing, the certificates are tried against the server's TLS. A file that would not
    verify the server, e.g. because the host name in I(url) is not on its certificate, is not
    written.
version_added: 0.1.0
author: Michael Billings
extends_documentation_fragment: [yoki.netbackup.connection]
options:
  dest:
    description: Where to write the PEM file.
    type: path
    required: true
  fingerprint:
    description:
      - The CA's SHA-256 or SHA-1 fingerprint as NetBackup shows it (colons and case do not
        matter). Several may be given.
    type: list
    elements: str
  accept_new:
    description:
      - Trust a CA seen for the first time without a fingerprint to compare. Only where the
        network path to the primary is trusted.
    type: bool
    default: false
notes:
  - "I(api_key), I(username), I(ca_bundle) and I(validate_certs) are accepted (so one
    C(module_defaults) block serves every module) but not used: no login is needed, and the
    download is checked by fingerprint, not by a CA."
"""

EXAMPLES = r"""
- name: The primary's CA, checked against the fingerprint read on the primary
  yoki.netbackup.nbu_ca_cert:
    url: "https://{{ nbu_primary }}:1556/netbackup"
    dest: "out/ca/{{ nbu_primary }}.pem"
    fingerprint: "{{ nbu_ca_fingerprint }}"
  delegate_to: localhost
"""

RETURN = r"""
dest:
  description: The PEM file.
  returned: always
  type: str
certificates:
  description: Each CA certificate in the answer, with C(subject), C(not_after), C(sha256) and C(sha1).
  returned: always
  type: list
verified:
  description: The certificates verified the server's TLS.
  returned: success
  type: bool
"""

import base64
import hashlib
import os
import re
import tempfile

from ansible.module_utils.basic import AnsibleModule, missing_required_lib

from ansible_collections.yoki.netbackup.plugins.module_utils.nbu import (
    CONNECTION_ARGS,
    HAS_SDK,
    ON_THE_CONTROLLER,
    SDK_IMPORT_ERROR,
)

PEM = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----")
NOT_ACCEPTABLE = 406


def pems_in(value) -> list[str]:
    """Every PEM certificate anywhere in a decoded JSON answer (or text), in
    order, once each: the answer's shape differs between releases."""
    found: list[str] = []

    def walk(v) -> None:
        if isinstance(v, str):
            for pem in PEM.findall(v):
                pem = normalise(pem)
                if pem not in found:
                    found.append(pem)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return found


def normalise(pem: str) -> str:
    body = "".join(pem.split("-----")[2].split())
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"


def der(pem: str) -> bytes:
    return base64.b64decode("".join(pem.split("-----")[2].split()))


def colons(digest: str) -> str:
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def describe(pem: str) -> dict:
    raw = der(pem)
    info = {
        "sha256": colons(hashlib.sha256(raw).hexdigest()),
        "sha1": colons(hashlib.sha1(raw).hexdigest()),  # noqa: S324 - a fingerprint, as NetBackup shows it
    }
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(raw)
        info["subject"] = cert.subject.rfc4514_string()
        info["not_after"] = cert.not_valid_after_utc.isoformat()
    except Exception:  # descriptive only; the fingerprints are what count
        pass
    return info


def plain(fp: str) -> str:
    return re.sub(r"[^0-9A-F]", "", fp.upper())


def fetch(url: str, timeout: float):
    """The cacert answer, unverified (it is what verification needs), at
    the newest API version the server accepts."""
    import httpx
    from netbackup_sdk.versions import AUTO_ORDER, media_type

    with httpx.Client(verify=False, timeout=timeout) as client:  # noqa: S501 - checked by fingerprint
        for version in AUTO_ORDER:
            response = client.get(f"{url}/security/cacert", headers={"Accept": media_type(version)})
            if response.status_code != NOT_ACCEPTABLE:
                response.raise_for_status()
                return response.json()
    raise RuntimeError("the server accepted none of the API versions " + ", ".join(AUTO_ORDER))


def tls_check(url: str, bundle: str, timeout: float) -> str | None:
    """None if `bundle` verifies the server's TLS, else why not."""
    import httpx
    from netbackup_sdk.client import tls_context

    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
        fh.write(bundle)
    try:
        with httpx.Client(verify=tls_context(fh.name), timeout=timeout) as client:
            client.get(f"{url}/security/cacert", headers={"Accept": "*/*"})
    except httpx.HTTPError as error:
        return str(error)
    finally:
        os.unlink(fh.name)
    return None


def main() -> None:
    module = AnsibleModule(
        argument_spec={
            **CONNECTION_ARGS,
            "dest": {"type": "path", "required": True},
            "fingerprint": {"type": "list", "elements": "str"},
            "accept_new": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    if not HAS_SDK:
        module.fail_json(
            msg=missing_required_lib("netbackup-sdk") + ON_THE_CONTROLLER,
            exception=SDK_IMPORT_ERROR,
        )
    p = module.params
    url, dest, timeout = p["url"].rstrip("/"), p["dest"], p["timeout"]

    try:
        pems = pems_in(fetch(url, timeout))
    except Exception as error:
        module.fail_json(msg=f"Reading {url}/security/cacert failed: {error}")
    if not pems:
        module.fail_json(msg=f"{url}/security/cacert held no PEM certificate")
    certificates = [describe(pem) for pem in pems]
    bundle = "".join(pems)
    result = {"dest": dest, "certificates": certificates}

    wanted = {plain(fp) for fp in p["fingerprint"] or []}
    matches = any(plain(c["sha256"]) in wanted or plain(c["sha1"]) in wanted for c in certificates)
    if wanted and not matches:
        module.fail_json(
            msg="The CA the server sent does not match the fingerprint given. Do not trust it; "
            "check the fingerprint on the primary (nbcertcmd -displayCACertDetail).",
            **result,
        )

    pinned = None
    if os.path.exists(dest):
        with open(dest, encoding="utf-8") as fh:
            pinned = pems_in(fh.read())
        if sorted(pinned) == sorted(pems):
            module.exit_json(
                changed=False, verified=tls_check(url, bundle, timeout) is None, **result
            )
        if not matches:
            module.fail_json(
                msg=f"The server's CA is not the one pinned in {dest}: it was renewed, or something "
                "is in between. If it was renewed, check the new fingerprint on the primary "
                "(nbcertcmd -displayCACertDetail) and give it as fingerprint.",
                **result,
            )
    elif not (matches or p["accept_new"]):
        module.fail_json(
            msg="First contact: compare these fingerprints with the primary's "
            "(nbcertcmd -displayCACertDetail on the primary), then run again with the matching "
            "one as fingerprint (or accept_new=true if the path to the primary is trusted).",
            **result,
        )

    problem = tls_check(url, bundle, timeout)
    if problem:
        module.fail_json(
            msg=f"These certificates do not verify {url}: {problem}. If the error is about the "
            "host name, use the name on the server's certificate in url (often the FQDN). "
            f"Nothing was written to {dest}.",
            **result,
        )
    if not module.check_mode:
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)), suffix=".pem")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(bundle)
        os.chmod(tmp, 0o644)  # a public certificate
        module.atomic_move(tmp, dest)
    module.exit_json(changed=True, verified=True, **result)


if __name__ == "__main__":
    main()
