#!/usr/bin/env python3
"""Diagnostic script to check worker container status and logs.

This script helps diagnose why worker containers are failing to start.
"""
import docker
import sys


def diagnose_workers():
    """Check worker container status and logs."""
    client = docker.from_env()

    print("=" * 80)
    print("WORKER CONTAINER DIAGNOSTICS")
    print("=" * 80)
    print()

    # Check for CLX network
    print("1. Checking Docker network...")
    print("-" * 80)
    try:
        network = client.networks.get('clx_app-network')
        print(f"✓ Network 'clx_app-network' exists (ID: {network.id[:12]})")
    except docker.errors.NotFound:
        print("✗ ERROR: Network 'clx_app-network' not found!")
        print("  Solution: Create the network with:")
        print("    docker network create clx_app-network")
        print()
    except Exception as e:
        print(f"✗ ERROR checking network: {e}")
        print()

    # Check for worker containers
    print("\n2. Checking worker containers...")
    print("-" * 80)

    containers = client.containers.list(
        all=True,
        filters={"name": "clx-"}
    )

    if not containers:
        print("No CLX worker containers found.")
        print("This is expected if you haven't started the pool manager yet.")
        return

    print(f"Found {len(containers)} CLX container(s):\n")

    for container in containers:
        print(f"Container: {container.name}")
        print(f"  Status: {container.status}")
        print(f"  Image: {container.image.tags[0] if container.image.tags else container.image.id[:12]}")

        # Get container logs
        print(f"  Logs (last 50 lines):")
        try:
            logs = container.logs(tail=50).decode('utf-8', errors='replace')
            if logs.strip():
                for line in logs.strip().split('\n'):
                    print(f"    {line}")
            else:
                print("    (no logs)")
        except Exception as e:
            print(f"    ERROR getting logs: {e}")

        print()

    # Check Docker images
    print("\n3. Checking Docker images...")
    print("-" * 80)

    expected_images = [
        'notebook-processor:0.2.2',
        'drawio-converter:0.2.2',
        'plantuml-converter:0.2.2'
    ]

    for image_name in expected_images:
        try:
            image = client.images.get(image_name)
            print(f"✓ {image_name} exists (ID: {image.id[:19]})")
        except docker.errors.ImageNotFound:
            print(f"✗ {image_name} NOT FOUND")
        except Exception as e:
            print(f"✗ Error checking {image_name}: {e}")

    print()
    print("=" * 80)
    print("DIAGNOSTICS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    try:
        diagnose_workers()
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
