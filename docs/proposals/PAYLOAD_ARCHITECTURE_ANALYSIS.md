# Analysis: Payload Architecture and Large File Handling

## Current Architecture

### How Payloads Work

1. **Payload Creation** (`process_notebook.py`):
   ```python
   payload = NotebookPayload(
       data=self.input_file.path.read_text(encoding="utf-8"),  # Full notebook content
       other_files=self.compute_other_files(),  # ALL supporting files, base64-encoded
       ...
   )
   ```

2. **`other_files` Contents** - ALL non-image files from the topic directory:
   ```python
   def compute_other_files(self):
       other_files = {
           relative_path(file): b64encode(file.path.read_bytes())
           for file in self.input_file.topic.files
           if file != self.input_file
           and not is_image_file(file.path)
           and not is_image_source_file(file.path)
           and not is_ignored_file_for_course(file.path)
       }
       return other_files
   ```

3. **Why `other_files` Exist**: Notebooks need supporting files during execution (e.g., `pd.read_csv("data.csv")`, `pickle.load(open("model.pkl"))`). The worker writes these to a temp directory before executing the notebook.

4. **Data Flow**:
   ```
   Source Files → Base64 Encode → JSON Payload → SQLite → REST API → Worker → Base64 Decode → Temp Directory
   ```

### Current Docker Volume Configuration

```python
volumes={
    str(self.workspace_path.absolute()): {"bind": "/workspace", "mode": "rw"},
}
```

**Only the OUTPUT directory is mounted.** Workers have NO access to source files.

## The Problem with Large Files

For ML courses with large datasets:

| File Type | Typical Size | Base64 Overhead | Total in Payload |
|-----------|--------------|-----------------|------------------|
| Notebook | 50 KB | +17 KB | 67 KB |
| CSV dataset | 100 MB | +33 MB | 133 MB |
| Pickle model | 500 MB | +167 MB | 667 MB |
| Full dataset | 2 GB | +667 MB | 2.7 GB |

**Issues:**
1. **Memory pressure**: Entire payload loaded into memory for encoding/decoding
2. **SQLite bloat**: Massive JSON blobs in database
3. **Network transfer**: REST API transmits entire payload
4. **Redundant transfers**: Same file transferred for each output variant (14+ per slide file)

## Options Analysis

### Option A: Mount Source Directory (Recommended)

**Change:** Mount both source directory (read-only) and output directory (read-write).

```python
volumes={
    str(self.data_dir.absolute()): {"bind": "/source", "mode": "ro"},
    str(self.workspace_path.absolute()): {"bind": "/workspace", "mode": "rw"},
}
```

**Payload Changes:**
- Remove `data` field (or make optional for backward compatibility)
- Remove `other_files` field
- Add `source_root` field with converted path to `/source`
- Workers read from `/source/...` paths

**Pros:**
- No payload size issues regardless of file size
- No base64 encoding overhead
- No redundant data transfer
- Files only read when needed
- Simple conceptual model

**Cons:**
- Need to convert input paths (similar to current output path conversion)
- Source directory exposed to container (read-only mitigates risk)
- Breaking change to payload format

**Implementation Effort:** Medium
- Update `worker_executor.py` to mount two volumes
- Update path conversion to handle input paths
- Update workers to read from filesystem
- Update payload classes to make `data`/`other_files` optional

### Option B: Keep Payload for Small Files, Mount for Large

**Change:** Hybrid approach - include small files (<1MB) in payload, require filesystem access for large files.

**Pros:**
- Backward compatible for most cases
- Large files handled efficiently

**Cons:**
- Complex logic to decide what goes where
- Workers need conditional code paths
- Size threshold arbitrary and hard to tune
- Still need to mount source for large file cases

**Implementation Effort:** High (complexity)

### Option C: Lazy File Loading via API

**Change:** Instead of including files in payload, provide API endpoints to fetch file contents on demand.

```python
# New API endpoint
GET /api/files/{path}  # Returns file contents
```

**Pros:**
- Files only transferred when needed
- No upfront payload size issues

**Cons:**
- Many HTTP requests during notebook execution
- Complex worker implementation
- Network latency for each file access
- Still transfers data, just lazily

**Implementation Effort:** High

### Option D: Shared Storage / NFS

**Change:** Use network storage accessible from both host and container.

**Pros:**
- No data transfer at all
- Works with any file size

**Cons:**
- Requires infrastructure setup
- Platform-specific configuration
- Not portable

**Implementation Effort:** Low (code), High (infrastructure)

## Recommendation: Option A (Mount Source Directory)

### Rationale

1. **Simplest conceptual model**: Workers have filesystem access, period
2. **Handles all file sizes**: No special cases for large files
3. **Efficient**: No encoding, no transfer overhead
4. **Consistent with direct mode**: Both modes work the same way
5. **Already have path conversion**: We just fixed output paths; input paths are similar

### Proposed Implementation

#### Phase 1: Add Source Mount

```python
# worker_executor.py
volumes={
    str(self.data_dir.absolute()): {"bind": "/source", "mode": "ro"},
    str(self.workspace_path.absolute()): {"bind": "/workspace", "mode": "rw"},
}

environment={
    "CLM_HOST_DATA_DIR": str(self.data_dir.absolute()),  # For input path conversion
    "CLM_HOST_WORKSPACE": str(self.workspace_path.absolute()),  # For output path conversion
    ...
}
```

#### Phase 2: Update Path Conversion

```python
# worker_base.py
CONTAINER_SOURCE = "/source"
CONTAINER_WORKSPACE = "/workspace"

def convert_input_path_to_container(host_path: str, host_data_dir: str) -> Path:
    """Convert host input path to container /source path."""
    # Similar to convert_host_path_to_container but for /source
    ...

def convert_output_path_to_container(host_path: str, host_workspace: str) -> Path:
    """Convert host output path to container /workspace path."""
    # Existing function, renamed for clarity
    ...
```

#### Phase 3: Update Workers

```python
# notebook_worker.py
async def _process_job_async(self, job: Job):
    payload_data = job.payload

    # Determine if running in Docker mode
    host_data_dir = os.environ.get("CLM_HOST_DATA_DIR")

    if host_data_dir:
        # Docker mode: read from mounted source
        input_path = convert_input_path_to_container(job.input_file, host_data_dir)
        notebook_text = input_path.read_text(encoding="utf-8")
    else:
        # Direct mode: use payload data (backward compatible)
        notebook_text = payload_data.get("data")
        if not notebook_text:
            # Fallback to reading from filesystem
            notebook_text = Path(job.input_file).read_text(encoding="utf-8")
```

#### Phase 4: Handle `other_files`

For Docker mode, workers would read supporting files directly from `/source`:

```python
# Instead of writing base64-decoded files to temp dir,
# workers can access them at their original locations under /source

# Current (payload-based):
for extra_file, encoded_contents in payload.other_files.items():
    contents = b64decode(encoded_contents)
    (temp_dir / extra_file).write_bytes(contents)

# New (filesystem-based in Docker):
# Just use /source/{topic_path}/ as the working directory for execution
# Files are already there via the mount
```

### Migration Path

1. **Add source mount** without changing worker behavior (backward compatible)
2. **Add input path conversion** function
3. **Update workers** to prefer filesystem over payload when available
4. **Deprecate** `data` and `other_files` in payload (keep for backward compatibility)
5. **Eventually remove** payload data transfer (major version)

### Risk Mitigation

- **Source exposure**: Mount as read-only; containers can't modify source
- **Path conflicts**: Clear naming (`/source` vs `/workspace`)
- **Backward compatibility**: Keep payload fields optional; workers check filesystem first

## Summary

| Aspect | Current (Payload) | Proposed (Mount) |
|--------|-------------------|------------------|
| Large files | Problematic | No issues |
| Memory usage | High (full payload in RAM) | Low (stream files) |
| Network transfer | Entire payload | None |
| SQLite storage | Massive JSON blobs | Small metadata |
| Complexity | Medium | Medium |
| Docker setup | Single mount | Two mounts |

**Recommendation**: Implement Option A (mount source directory) to fundamentally solve the large file problem while simplifying the architecture.
