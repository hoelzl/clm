# %%
import ctypes
import logging
from concurrent.futures import ThreadPoolExecutor
import os
import tkinter as tk
import tkinter.ttk as ttk

from clm.core.course import Course

# %%
_MAX_WORKERS = 4
_APP_TITLE = "Coding-Academy Lecture Manager"


# %%
class LectureManager:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        self._variables = []

        self.course: Course | None = None

        self.root = self._create_root_frame()
        self.main_frame = self._create_main_frame()
        menubar, file_menu, edit_menu, help_menu = self._create_menubar()
        self.menubar = menubar
        self.file_menu = file_menu
        self.edit_menu = edit_menu
        self.help_menu = help_menu

    def _create_root_frame(self):
        root = tk.Tk()
        self._fix_scaling(root)
        root.title = _APP_TITLE
        root.columnconfigure(1, weight=1, minsize=200)
        root.rowconfigure(1, weight=1)
        root.option_add("*tearOff", tk.FALSE)
        return root

    @staticmethod
    def _fix_scaling(root):
        """Scale correctly on HiDPI displays.

        This is a modified version of the `fix_scaling()` function from IDLE.
        """
        import tkinter.font

        root.tk.call("tk", "scaling", 2.0)

        scaling = float(root.tk.call("tk", "scaling"))
        if scaling > 1.4:
            for name in tkinter.font.names(root):
                font = tkinter.font.Font(root=root, name=name, exists=True)
                size = int(font["size"])
                if size < 0:
                    font["size"] = round(-0.75 * size)

    def _create_main_frame(self):
        main_frame = ttk.Frame(self.root, padding="3 3 12 12")
        main_frame.grid(column=1, row=1, sticky="NSEW")
        main_frame.columnconfigure(1, weight=1, minsize=200)
        main_frame.columnconfigure(1, weight=2, minsize=200)
        main_frame.rowconfigure(999, weight=1)
        return main_frame

    def _create_menubar(self):
        menubar = tk.Menu(self.root)

        file_menu = self._create_file_menu(menubar)
        edit_menu = self._create_edit_menu(menubar)
        help_menu = self._create_help_menu(menubar)

        self.root["menu"] = menubar
        return menubar, file_menu, edit_menu, help_menu

    def _create_file_menu(self, menubar):
        file_menu = tk.Menu(menubar)
        file_menu.add_command(label="New Course...", command=self.new_course)
        file_menu.add_command(label="Open Course...", command=self.open_course)
        file_menu.add_command(label="Save Course", command=self.save_course)
        file_menu.add_command(label="Save Course As...", command=self.save_course_as)
        menubar.add_cascade(menu=file_menu, label="File")
        return file_menu

    def _create_edit_menu(self, menubar):
        edit_menu = tk.Menu(menubar)
        edit_menu.add_command(label="Copy", command=self.copy)
        edit_menu.add_command(label="Paste", command=self.paste)
        menubar.add_cascade(menu=edit_menu, label="Edit")
        return edit_menu

    def _create_help_menu(self, menubar):
        help_menu = tk.Menu(menubar)
        help_menu.add_command(label="About", command=self.about)
        menubar.add_cascade(menu=help_menu, label="Help")
        return help_menu

    def new_course(self) -> None:
        print("Creating a new course.")

    def open_course(self) -> None:
        print("Opening a course.")

    def save_course(self) -> None:
        print("Saving course.")

    def save_course_as(self) -> None:
        print("Saving course as...")

    def copy(self) -> None:
        print("Copying course data.")

    def paste(self) -> None:
        print("Pasting course data.")

    def about(self) -> None:
        print("About CLM.")

    def run(self) -> None:
        self.root.mainloop()


# %%
if __name__ == "__main__":
    app = LectureManager()
    app.run()
