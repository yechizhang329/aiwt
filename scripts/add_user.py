#!/usr/bin/env python3
"""
Add a user to config/users.yaml.

Usage:
  python3 scripts/add_user.py --username assistant_b --name "助理B" \
      --email b@example.com --role user

  python3 scripts/add_user.py --username admin2 --name "管理员2" \
      --email admin2@example.com --role admin

Roles: user (default) | admin

The script will:
1. Prompt for a password (hidden input)
2. Generate a bcrypt hash
3. Print the YAML block to paste into config/users.yaml
4. Optionally write directly to users.yaml if --write flag is given
"""

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Add a user to users.yaml")
    parser.add_argument("--username", required=True, help="Login username (no spaces)")
    parser.add_argument("--name",     required=True, help="Display name (Chinese OK)")
    parser.add_argument("--email",    required=True, help="Email address")
    parser.add_argument("--role",     default="user", choices=["user", "admin"],
                        help="Role: user (default) or admin")
    parser.add_argument("--write",    action="store_true",
                        help="Write directly to config/users.yaml (no manual paste needed)")
    args = parser.parse_args()

    if " " in args.username:
        print("Error: username must not contain spaces", file=sys.stderr)
        sys.exit(1)

    # Prompt for password
    pw1 = getpass.getpass(f"Password for {args.username}: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Error: passwords do not match", file=sys.stderr)
        sys.exit(1)
    if len(pw1) < 8:
        print("Error: password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)

    try:
        import bcrypt
    except ImportError:
        print("Error: bcrypt not installed. Run: pip3 install bcrypt", file=sys.stderr)
        sys.exit(1)

    hashed = bcrypt.hashpw(pw1.encode(), bcrypt.gensalt()).decode()

    yaml_block = (
        f"    {args.username}:\n"
        f"      email: {args.email}\n"
        f"      name: {args.name}\n"
        f"      password: \"{hashed}\"\n"
        f"      role: {args.role}\n"
    )

    if args.write:
        users_yaml = ROOT / "config" / "users.yaml"
        import yaml
        with open(users_yaml) as f:
            cfg = yaml.safe_load(f)
        if args.username in cfg["credentials"]["usernames"]:
            print(f"Error: username '{args.username}' already exists", file=sys.stderr)
            sys.exit(1)
        cfg["credentials"]["usernames"][args.username] = {
            "email": args.email,
            "name": args.name,
            "password": hashed,
            "role": args.role,
        }
        with open(users_yaml, "w") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print(f"✅ User '{args.username}' added to config/users.yaml")
        print("Restart Streamlit for the change to take effect.")
    else:
        print("\n--- Paste this block under 'credentials.usernames:' in config/users.yaml ---\n")
        print(yaml_block)
        print("--- Then restart Streamlit ---\n")


if __name__ == "__main__":
    main()
