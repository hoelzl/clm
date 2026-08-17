- The course-kernel provisioning hash (`%LOCALAPPDATA%\clm\kernel-envs\<hash>`)
  is now taken over an `os.path.normcase`'d form of the interpreter path, so
  spelling variants of the same venv (`C:\...` vs `c:/...`, forward vs
  backward slashes) share one kernelspec dir instead of provisioning
  duplicate twins (follow-up to the #853 incident forensics). Symlinks are
  deliberately not resolved — on POSIX `.venv/bin/python` links into the base
  interpreter, and resolving it would launch the kernel outside the venv.
