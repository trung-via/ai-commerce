"""Development transport entry point. Production uses the ACL-protected pinned release."""
from ai_commerce.hostops_v3.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
