import logging
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from attr import define, field
from jupytext import jupytext
from nbformat import NotebookNode

from clm.core.data_sink import DataSink
from clm.core.data_source_location import full_target_location_for_data_source
from clm.core.output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.data_sources.notebook_data_source import NotebookDataSource

from clm.utils.config import config
from clm.utils.jupyter_utils import (
    Cell,
    get_tags,
    is_code_cell,
)


@define
class EditscriptDataSink(DataSink["NotebookDataSource"]):
    expanded_notebook: str = field(default="", repr=False)
    edit_script: list[str] = field(factory=list, repr=False)
    output_text: list[str] = field(factory=list, repr=False)
    diff_start: str | None = field(default=None, repr=False)

    def process(
        self, doc: "NotebookDataSource", expanded_nb: str, output_spec: OutputSpec
    ) -> None:
        self.expanded_notebook = expanded_nb
        try:
            logging.info(f"Reading notebook as {self.jupytext_format}")
            nb = jupytext.reads(expanded_nb, fmt=self.jupytext_format)
            self.process_notebook(nb, output_spec)
        except Exception as err:
            logging.error(f"Failed to process notebook {doc.source_loc}")
            logging.error(err)

    @property
    def jupytext_format(self) -> str:
        if self.data_source.prog_lang not in config["prog_lang"]:
            raise ValueError(
                f"Unknown programming language {self.data_source.prog_lang!r}."
            )
        if "jupytext_format" not in config["prog_lang"][self.data_source.prog_lang]:
            raise ValueError(
                f"Programming language {self.data_source.prog_lang!r} has no "
                f"jupytext_format in config."
            )
        return config["prog_lang"][self.data_source.prog_lang]["jupytext_format"]

    def process_notebook(self, nb_node: NotebookNode, output_spec: OutputSpec) -> None:
        for index, cell in enumerate(nb_node.get("cells", [])):
            self.process_cell(cell, output_spec)

    def process_cell(self, cell: Cell, output_spec: OutputSpec) -> None:
        logging.debug(f"Processing cell {cell}")
        if is_code_cell(cell) and output_spec.is_cell_included(cell):
            logging.debug(">> Cell is retained code cell")
            return self.process_code_cell(cell)

    def process_code_cell(self, cell: Cell) -> None:
        logging.debug(f"Processing code cell {cell}")
        if "start" in get_tags(cell):
            assert self.diff_start is None, "Multiple start cells found."
            self.diff_start = cell.source
        elif self.diff_start is not None:
            diff_script = compute_edit_script(self.diff_start, cell.source)
            self.diff_start = None
            self.edit_script.append(diff_script)
            self.output_text.append(
                encode_for_ahk_script("DIFF SCRIPT FOR:\n" + cell.source)
            )
        else:
            self.edit_script.append(encode_for_diff_script(cell.source))
            self.output_text.append(encode_for_ahk_script(cell.source))

    def write_to_target(self):
        target_loc = full_target_location_for_data_source(
            self.data_source, self.course, self.output_spec
        )
        logging.info(f"Writing editscript to {target_loc}")
        target_loc.parent.mkdir(parents=True, exist_ok=True)
        with target_loc.open("w", encoding="utf-8") as file:
            file.write(ahk_prefix)
            file.write("typer := RemoteTyper(")
            file.write("    [\n")
            for text_block in self.output_text:
                file.write(f'        "{text_block}",\n')
            file.write("    ],\n")
            file.write("    [\n")
            for line in self.edit_script:
                file.write(f'        "{line}",\n')
            file.write("    ]\n)\n")
            file.write(ahk_postfix)


def compute_edit_script(source: str, target: str) -> str:
    matcher = SequenceMatcher(None, source, target)
    opcodes = matcher.get_opcodes()
    return convert_opcodes_to_edit_script(opcodes, source, target)


def convert_opcodes_to_edit_script(opcodes, source, target):
    return "".join(
        convert_opcode_to_efficient_edit_script(opcode, source, target)
        for opcode in opcodes
    )
    # return "".join(convert_opcode_to_edit_script(opcode, target) for opcode in opcodes)


# def convert_opcode_to_edit_script(opcode, target):
#     print(f"Opcode: {opcode}, target: {target!r}")
#     tag, i1, i2, j1, j2 = opcode
#     if tag == "equal":
#         return f"{{Right {i2 - i1}}}"
#     elif tag == "replace":
#         replacement = encode_for_diff_script(target[j1:j2])
#         return f"{{Delete {i2 - i1}}}{replacement}"
#     elif tag == "delete":
#         return f"{{Delete {i2 - i1}}}"
#     elif tag == "insert":
#         insertion = encode_for_diff_script(target[j1:j2])
#         return f"{insertion}"
#     else:
#         raise ValueError(f"Unknown tag {tag!r} in opcode {opcode!r}")


def convert_opcode_to_efficient_edit_script(opcode, source, target):
    # print(f"Opcode: {opcode}, target: {target!r}")
    tag, i1, i2, j1, j2 = opcode
    if tag == "equal":
        newlines, indent = get_newlines_and_indent(source, target, i1, i2, j1)
        return movement_for_newlines_and_indent(newlines, indent)
    elif tag == "replace":
        replacement = encode_for_diff_script(target[j1:j2])
        return f"{{Delete {i2 - i1}}}{replacement}"
    elif tag == "delete":
        return f"{{Delete {i2 - i1}}}"
    elif tag == "insert":
        insertion = encode_for_diff_script(target[j1:j2])
        return f"{insertion}"
    else:
        raise ValueError(f"Unknown tag {tag!r} in opcode {opcode!r}")


def get_newlines_and_indent(
    source: str, target: str, i1: int, i2: int, j1: int
) -> tuple[int, int]:
    # We have already performed the edits on the first part of the string,
    # therefore we need to splice the second half of source onto the
    # part of the target that represents the processed part of the result.
    edit_len = i2 - i1
    j2 = j1 + edit_len
    edit_source = target[:j1] + source[i1:]
    newlines = edit_source[j1:j2].count("\n")
    if newlines == 0:
        return 0, edit_len
    newline_before_j1 = edit_source.rfind("\n", 0, j1)
    if newline_before_j1 == -1:
        start_index = 0
        j1_indent = j1
    else:
        start_index = newline_before_j1 + 1
        j1_indent = j1 - newline_before_j1 - 1
    newline_after_j2 = edit_source.find("\n", j2)
    if newline_after_j2 == -1:
        end_index = len(edit_source)
    else:
        end_index = newline_after_j2
    line_lengths = [
        len(line) for line in edit_source[start_index:end_index].split("\n")
    ]
    indent_after_moving_down = min(j1_indent, *line_lengths)
    last_newline = edit_source.rfind("\n", 0, j2)
    target_indent = j2 - last_newline - 1
    required_indent = target_indent - indent_after_moving_down
    return newlines, required_indent


def movement_for_newlines_and_indent(newlines: int, indent: int) -> str:
    left_right = ""
    if indent > 0:
        left_right = f"{{Right {indent}}}"
    elif indent < 0:
        left_right = f"{{Left {-indent}}}"
    down = f"{{Down {newlines}}}" if newlines > 0 else ""
    return down + left_right


def encode_for_ahk_script(source: str) -> str:
    for char, replacement in escape_chars_for_ahk_string.items():
        source = source.replace(char, replacement)
    return source


def encode_for_diff_script(source: str) -> str:
    for char, replacement in escape_chars_for_diff_script.items():
        source = source.replace(char, replacement)
    return source


escape_chars_for_ahk_string: dict[str, str] = {
    '"': '`"',
    "\n\r": "`n",
    "\r\n": "`n",
    "\n": "`n",
    "\r": "`n",
    "\t": "`t",
    "\b": "`t",
}

escape_chars_for_diff_script: dict[str, str] = {
    # Use a unicode character from a private use area as a temporary stand-in for `}` to
    # work around the fact that wer're doing sequential replacements.
    "{": "{{\uE001",
    "}": "{}}",
    "\uE001": "}",
    '"': '`"{Del}',
    "(": "({Del}",
    "\n\r": "{Enter}",
    "\r\n": "{Enter}",
    "\n": "{Enter}",
    "\r": "{Enter}",
    "\t": "{Tab}",
    "\b": "{Backspace}",
    # Temporary hack to avoid problems with auto indent
    "{Enter}": "{Enter}{Home}",
    "!": "{!}",
    "#": "{#}",
    "+": "{+}",
    "^": "{^}",
}

ahk_prefix = """\
class RemoteTyper {
    __new(outputText, editScript) {
        this.OutputText := outputText
        this.EditScript := editScript
        this.CurrentIndex := 1
        this.LastMagicCommandWasSend := false
        
        this.Gui := Gui("+DPIScale", "Remote Typer")
        this.Gui.SetFont("s20")

        this.Gui.Add("Text", "Section w120", "Previous:")
        this.PreviousTextBox := this.Gui.Add("Edit", "x+m w800 r3")

        this.Gui.Add("Text", "xm w120" , "Current:")
        this.CurrentTextBox := this.Gui.Add("Edit", "x+m w800 r6")

        this.Gui.Add("Text", "xm w120", "Next:")
        this.NextTextBox := this.Gui.Add("Edit", "x+m w800 r3")

        this.Gui.Add("Text", "xm w120", "Next++:")
        this.NextButOneTexBox := this.Gui.Add("Edit", "x+m w800 r3")

        this.Gui.Add("Button", "Section", "Prev").OnEvent("Click", (*) => this.MovePrevious())
        this.Gui.Add("Button", "x+m", "Next").OnEvent("Click", (*) => this.MoveNext())
        this.Gui.Add("Button", "x+500", "Ok").OnEvent("Click", this.Quit)

        this.UpdateGui()
    }

    PreviousText {
        get {
            if (this.CurrentIndex >= 2 && this.CurrentIndex <= this.OutputText.Length) {
                return this.OutputText[this.CurrentIndex - 1]
            }
            return ""
        }
    }

    CurrentText {
        get {
            return this.OutputText[this.CurrentIndex]
        }
    }

    NextText {
        get {
            if (this.CurrentIndex <=  this.OutputText.Length - 1) {
                return this.OutputText[this.CurrentIndex + 1]
            }
            return ""
        }
    }

    NextButOneText {
        get {
            if (this.CurrentIndex <= this.OutputText.Length - 2) {
                return this.OutputText[this.CurrentIndex + 2]
            }
            return ""
        }
    }

    CurrentEditScript {
        get {
            return this.EditScript[this.CurrentIndex]
        }
    }

    SendCurrentEditScript() {
        SendEvent("{Esc}{Enter}")
        SetKeyDelay(2, 6)
        SendEvent(this.CurrentEditScript)
    }

    SendCurrentEditScriptAndNext() {
        this.SendCurrentEditScript()
        this.MoveNext()
    }
    
    PerformMagicCommand() {
        if (this.LastMagicCommandWasSend) {
            SendEvent("{Esc}+{Enter}")
            this.LastMagicCommandWasSend := false
        } else {
            this.SendCurrentEditScriptAndNext()
            this.LastMagicCommandWasSend := true
        }
    }

    MoveNext() {
        if (this.CurrentIndex < this.OutputText.Length) {
            this.CurrentIndex++
        }
        this.LastMagicCommandWasSend := false
        this.UpdateGui()
    }

    MovePrevious() {
        if (this.CurrentIndex > 1) {
            this.CurrentIndex--
        }
        this.LastMagicCommandWasSend := false
        this.UpdateGui()
    }

    UpdateGui() {
        this.PreviousTextBox.Value := this.PreviousText
        this.CurrentTextBox.Value := this.CurrentText
        this.NextTextBox.Value := this.NextText
        this.NextButOneTexBox.Value := this.NextButOneText
    }

    Show() {
        this.Gui.Show()
    }
    
    Quit(*) {
        this.Gui.Destroy()
        ExitApp()
    }
}
"""

ahk_postfix = """
SetNumLockState("Off")
typer.Show()

NumpadEnter::typer.PerformMagicCommand()

NumpadDot::typer.SendCurrentEditScriptAndNext()

Numpad0::typer.SendCurrentEditScript()

NumpadSub::typer.MovePrevious()

NumpadAdd::typer.MoveNext()
"""
