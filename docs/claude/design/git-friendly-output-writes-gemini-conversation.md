# you asked

OK, that's great. For a long term solution, can you tell me whether I can modify the external program to be more "git friendly." The reason for moving the git directory is the following:
The external program (CLM) generates lecture slides and other files in multiple languages and output formats from a common input format. In order that CLM doesn't have to track the state of the previously generated course, it does the following: When a course is recreated, it moves its `.git` directory to a temporary location, deletes the complete folder and writes if from scratch. Then it moves the `.git` directory back. This avoids any problems with renaming files or changing the order of files (which may lead to many files obtaining new names because the output files have a "serial number" at the start, e.g. "01 Introduction"). But obviously it's very bad for git performance.
Do you see a better way to handle this. For example, could we ask git for the directory contents and content hashes of the files that are in the repository, and then actually modify the existing repository instead of the brute force approach we are using now?

---

# gemini response

Yes, absolutely. The current "brute-force" design of the Course Lifecycle Manager (CLM) is a textbook anti-pattern for Git. By wiping and recreating the folder while hiding the `.git` directory, CLM forces Git to do a massive, expensive cryptographic re-verification of the entire project every single run.

You can modify CLM to be immensely more "Git-friendly" without losing its state-free design. Here are the three best ways to refactor the tool's output engine, ranging from simple to highly optimized.

---

## Strategy 1: The "Smart Purge" (Easiest to implement)

If you want to keep the logic where CLM doesn't track state, you can replace the `rm -rf *` step with a smart deletion that spares the `.git` folder but uses standard file system synchronization. 

Instead of moving `.git` away, use an efficient synchronization utility (like `rsync` or native equivalents in Python/Go/Node) to build the new course into a separate temporary directory, and then sync it to the target directory.

**The Workflow:**
1. CLM generates the entire course into a temporary directory (e.g., `/tmp/clm-build/`).
2. Run a synchronization command targeting the live repo:
   ```bash
   rsync -a --delete --exclude='.git/' /tmp/clm-build/ /path/to/your/repo/
   ```
3. Run Git commands normally.

**Why this helps:** `rsync` will only touch files that actually changed, completely preserving the `mtime`, size, and inodes of the 90% of files that stayed the same (even if serial numbers shifted further down the line). Git's stat cache remains completely intact for unchanged files, keeping `git status` sub-second.

---

## Strategy 2: Query Git for the State (Your suggestion)

If you want CLM to actively query Git so it can intelligently clean up after itself, Git has specific low-level plumbing commands designed exactly for scripts to read the index.

Before generating anything, CLM can get a complete manifest of everything Git currently tracks using:
```bash
git ls-files
```
If you also need the content hashes (SHA-1s) of those files directly from the index without reading the files on disk, use:
```bash
git ls-files --stage
```
This outputs entries like this:
```text
100644 7a31b2... 0    01_Introduction/slides.html
100644 e1a9f4... 0    02_Architecture/diagram.png
```

**The Workflow:**
1. **Gather Manifest:** CLM runs `git ls-files` to get an in-memory list of what exists right now.
2. **Generate Directly:** CLM overwrites files and writes new ones straight into the directory. It doesn't delete anything yet.
3. **Reconcile and Clean Up:** CLM looks at its *old* manifest. Any file that was in the old Git manifest but was *not* recreated by CLM in this run is a stray file (e.g., due to a renaming or serial number shift). CLM explicitly deletes only those stray files.
4. **Stage:** Run `git add -A` (which efficiently picks up the edits, creations, and deletions).

---

## Strategy 3: The "No-Working-Tree" Approach (Ultimate Performance)

If CLM is written in a language with good scripting capabilities, you can bypass the working tree entirely during generation. Git allows you to hash files directly into its object database and write a commit **without writing files to the disk first**.

If CLM computes the new slide contents, it can pass them directly to Git's plumbing:
1. **Hash content into Git:** 
   
```bash
   echo "slide content" | git hash-object -w --stdin
   ```
   This returns a SHA-1 hash and safely stores the object in `.git/objects`, bypassing the disk working directory.
2. **Build the Index:** CLM can use `git update-index --index-info` to feed a list of paths and their new hashes straight into the Git staging area.
3. **Commit:** CLM runs `git write-tree` and `git commit-tree` to cut a commit.
4. **Checkout:** Finally, you run a single `git checkout` or `git reset --hard` to update the physical disk files to match the new commit all at once.

---

## Summary Comparison

| Strategy | Complexity | Disk IO | Git Index Impact |
| :--- | :--- | :--- | :--- |
| **Current Brute-Force** | Very Low | Extremely High | **Trashes the index completely** |
| **1. Smart Purge (`rsync`)** | Low | Low (Updates only changes) | Perfect (Preserves untouched files) |
| **2. Query Git (`ls-files`)**| Medium | Low (Deletes strays only) | Great (Standard incremental updates) |
| **3. Plumbing (`index-info`)**| High | Extremely Low | Perfect (Index is built directly in-memory) |

**Recommendation:** Go with **Strategy 1** or **Strategy 2**. They allow CLM to remain "stateless" conceptually, but treat the filesystem and Git with the precision required to keep performance lightning-fast.
```