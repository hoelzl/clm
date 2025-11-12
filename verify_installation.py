#!/usr/bin/env python
"""Verify that all Phase 2 dependencies are installed correctly."""

import sys

def check_import(module_name, package_name=None):
    """Check if a module can be imported."""
    package_name = package_name or module_name
    try:
        __import__(module_name)
        print(f"✓ {package_name} is installed")
        return True
    except ImportError as e:
        print(f"✗ {package_name} is NOT installed: {e}")
        return False

def main():
    """Check all required dependencies."""
    print("Checking Phase 2 dependencies...\n")

    all_ok = True

    # Core dependencies
    all_ok &= check_import('pydantic')
    all_ok &= check_import('click')
    all_ok &= check_import('watchdog')

    # FastStream and RabbitMQ
    all_ok &= check_import('faststream')
    all_ok &= check_import('aio_pika', 'aio-pika')
    all_ok &= check_import('aiormq')

    # Phase 2 dependencies
    all_ok &= check_import('docker')

    # Testing dependencies
    all_ok &= check_import('pytest')
    all_ok &= check_import('pytest_asyncio', 'pytest-asyncio')
    all_ok &= check_import('pytest_mock', 'pytest-mock')

    # CLX packages
    all_ok &= check_import('clx_common')
    all_ok &= check_import('clx')
    all_ok &= check_import('clx_faststream_backend')
    all_ok &= check_import('clx_cli')

    print()
    if all_ok:
        print("✓ All dependencies are installed correctly!")
        return 0
    else:
        print("✗ Some dependencies are missing.")
        print("\nTo install missing dependencies, run:")
        print("  pip install -r requirements.txt")
        print("  pip install -e ./clx-common -e ./clx -e ./clx-faststream-backend -e ./clx-cli")
        return 1

if __name__ == "__main__":
    sys.exit(main())
