# Development Notes

Some notes and ideas about the development of the project.

## Preparation for multi-lingual support

- [ ] Extract generation of course specs into separate module
  - Class `DirectoryKind` that specifies
    - whether to recurse into subdirectories when generating the course spec
    - how files contained directly in this directory are to be classified
    - possibly how roles are assigned to subdirectories
  - Protocol `CourseLayout` that maps each directory into a `DirectoryKind`
    - Maybe we need multiple roles for a single directory
      - e.g., if we have roles for `image-soure` and `image-output` folders
        then we may want to assign both roles to the same directory
  - Protocol `CourseLayout`
    - `classify` method that takes a `Path` and returns a `Document` type
    - `Path` should be a directory (or even a larger structure) so that we can 
       identify dependencies. For example, if a `.drawio` file is present,
       a `.png` or `.svg` file with the same name is a generated file.
    - But maybe we can also move the tracking of generated files into a separate
      step.
    - `classify` needs additional information, in particular, a course layout
- [ ] Track dependencies between documents
  - References/includes: `D1` → `D2`
    - `D1` needs `D2` to be present in the generated output
    - `D2` should not appear in the course spec
    - If `D2` is generated, its sources should not appear in the course spec
  - Generated from: `D1` → `D2` (`Gen`)
    - `D1` is generated from `D2` using a generator `Gen`
    - `D2` should not appear in the course spec
- [ ] Build a task graph based on the dependency information
  - `Task` as an abstraction of something that needs to be done
  - Tasks may depend on the results of other tasks
    - Dependent tasks may thus be scheduled only after the dependencies have
      been completed
  - Tasks may generate other tasks that have to be scheduled later
    - e.g., the task to generate a notebook can not be scheduled until the
      order of files in its directory is known and the file name has been
      assigned (since the name includes the file position and is generated)
      from the contents of the file
    - We might generate incomplete tasks whose information is filled in by
      other tasks. E.g. the `build notebook` task might be generated as soon
      as we find the notebook file, but the missing information might not
      be filled in right away.