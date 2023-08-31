"""
Specs are descriptions of objects that can be edited as text.

A `DataSourceSpec` is a description of a single file that we process.
"""
from attr import frozen


@frozen
class DataSourceSpec:
    """A description how to build a data-source.

    Data-source specs are the intermediate representation from which we generate the
    data sources in a course.

    The idea is that we auto-generate a file containing data-source specs that can be
    manually edited to serve as input for the actual course. Therefore, we set the
    relative target dir to `"-"` which means "don't create an output for this
    source". For data_sources that should be included in the course, this value can then
    be changed to the actual subdirectory in which the generated file should live
    (e.g., "week1", "week2", etc. for online courses).
    """

    source_file: str
    target_dir_fragment: str
    label: str
    file_num: int

    def get_output_tuple(self):
        return self.source_file, self.target_dir_fragment, self.label
