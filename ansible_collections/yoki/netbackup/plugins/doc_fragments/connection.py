"""The connection options every yoki.netbackup module takes."""


class ModuleDocFragment:
    DOCUMENTATION = r"""
options:
  url:
    description:
      - The primary server's API, C(https://<primary>:1556/netbackup).
      - Falls back to the C(NBU_URL) environment variable.
    type: str
    required: true
  api_key:
    description:
      - An API key, sent as the C(Authorization) header. Preferred over a password.
      - Falls back to C(NBU_API_KEY).
    type: str
  username:
    description:
      - A user to log in with (C(POST /login), a 24-hour token renewed as needed), when no
        I(api_key) is given. Falls back to C(NBU_USERNAME).
    type: str
  password:
    description: The password for I(username). Falls back to C(NBU_PASSWORD).
    type: str
  domain_type:
    description:
      - The login domain type, C(vx) for a NetBackup user, C(unixpwd) for a local Linux user,
        C(nt) for Windows/Active Directory. Falls back to C(NBU_DOMAIN_TYPE).
    type: str
    default: vx
  domain_name:
    description: The domain, for C(nt) or LDAP users. Falls back to C(NBU_DOMAIN_NAME).
    type: str
  api_version:
    description:
      - C(auto) tries API 13.0 (NetBackup 11.0), then 12.0 (10.5), then 7.0 (10.0), and keeps
        the first one the server accepts.
      - A release (C(10.5)) or an API version (C(12.0)) pins it.
      - Falls back to C(NBU_API_VERSION).
    type: str
    default: auto
  ca_bundle:
    description:
      - NetBackup's CA certificate (PEM) to verify the server against. Falls back to
        C(NBU_CA_BUNDLE).
    type: path
  validate_certs:
    description:
      - Verify the server's certificate. Only for a first contact before the CA certificate is
        known. Falls back to C(NBU_VERIFY_TLS).
    type: bool
    default: true
  timeout:
    description: Seconds to wait for each request.
    type: float
    default: 120
  audit_reason:
    description: Sent as C(X-NetBackup-Audit-Reason) with every change, so NetBackup's audit log
      says why.
    type: str
    default: yoki.netbackup Ansible
notes:
  - "Runs on the controller, where yoki-py-netbackup-sdk is installed. Use
    C(delegate_to: localhost) or a C(connection: local) play."
  - In check mode the SDK client is built read-only, so nothing can be changed.
requirements:
  - netbackup-sdk (yoki-py-netbackup-sdk)
"""
