"""Quick diagnostic script to check job status."""
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else 'C:/Users/tc/AppData/Local/Temp/tmpht0la7cp.db'

try:
    db = sqlite3.connect(db_path)
    cursor = db.cursor()

    # Check jobs
    cursor.execute("SELECT id, job_type, status, worker_id, error FROM jobs ORDER BY id")
    jobs = cursor.fetchall()

    pending = [j for j in jobs if j[2] == 'pending']
    processing = [j for j in jobs if j[2] == 'processing']
    completed = [j for j in jobs if j[2] == 'completed']
    failed = [j for j in jobs if j[2] == 'failed']

    print(f"Total jobs: {len(jobs)}")
    print(f"  Pending: {len(pending)}")
    print(f"  Processing: {len(processing)}")
    print(f"  Completed: {len(completed)}")
    print(f"  Failed: {len(failed)}")

    if pending:
        print("\nFirst 5 pending jobs:")
        for job in pending[:5]:
            print(f"  Job {job[0]}: {job[1]} - worker {job[3]}")

    if processing:
        print("\nProcessing jobs:")
        for job in processing:
            print(f"  Job {job[0]}: {job[1]} - worker {job[3]}")

    if failed:
        print("\nFailed jobs:")
        for job in failed[:3]:
            print(f"  Job {job[0]}: {job[1]} - Error: {job[4]}")

    # Check workers
    cursor.execute("SELECT id, worker_type, status, last_heartbeat, jobs_processed FROM workers")
    workers = cursor.fetchall()

    print(f"\nWorkers: {len(workers)}")
    for worker in workers:
        print(f"  Worker {worker[0]} ({worker[1]}): {worker[2]} - last heartbeat: {worker[3]} - processed: {worker[4]}")

    db.close()
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
