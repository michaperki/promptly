
import sys
import os
import mimetypes
import subprocess
from collections import defaultdict
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog,
    QLabel, QTreeWidget, QTreeWidgetItem, QHBoxLayout, QLineEdit,
    QProgressBar, QMessageBox, QRadioButton, QButtonGroup, QCheckBox,
    QGroupBox, QScrollArea, QGridLayout, QSizePolicy, QSpacerItem
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QClipboard

class FileConcatenatorThread(QThread):
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    finished_successfully = pyqtSignal(str, list)  # Emit concatenated text and list of files

    def __init__(self, selected_paths, git_tracked=False, ignore_patterns=None, include_extensions=None):
        super().__init__()
        self.selected_paths = selected_paths
        self.git_tracked = git_tracked
        self.ignore_patterns = ignore_patterns if ignore_patterns else []
        self.include_extensions = set(ext.lower() for ext in include_extensions) if include_extensions else set()
        self.text_file_extensions = self.include_extensions  # Alias for clarity

    def run(self):
        try:
            # Gather all relevant files from selected paths
            all_files = []
            for path in self.selected_paths:
                if os.path.isfile(path):
                    if self.is_included_file(path):
                        if self.git_tracked:
                            if self.is_git_tracked(path):
                                all_files.append(path)
                        else:
                            all_files.append(path)
                elif os.path.isdir(path):
                    for root, dirs, files in os.walk(path, topdown=True):
                        # Modify dirs in-place to skip ignored directories
                        dirs[:] = [d for d in dirs if d not in self.ignore_patterns]
                        for file in files:
                            file_path = os.path.join(root, file)
                            if self.is_included_file(file_path):
                                if self.git_tracked:
                                    if self.is_git_tracked(file_path):
                                        all_files.append(file_path)
                                else:
                                    all_files.append(file_path)

            total_files = len(all_files)
            if total_files == 0:
                self.error_occurred.emit("No files found based on the selected preferences.")
                return

            self.status_update.emit("Starting concatenation...")
            concatenated_text = ""
            for index, file_path in enumerate(all_files, start=1):
                try:
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        content = infile.read()
                        # Prepend file name as a header
                        header = f"=== {os.path.basename(file_path)} ===\n"
                        concatenated_text += header + content + '\n\n'  # Separator between files
                except Exception as e:
                    self.error_occurred.emit(f"Error reading {file_path}: {str(e)}")
                    return
                progress_percent = int((index / total_files) * 100)
                self.progress_update.emit(progress_percent)
                self.status_update.emit(f"Processing {os.path.basename(file_path)} ({index}/{total_files})")

            self.status_update.emit("Concatenation completed successfully.")
            self.finished_successfully.emit(concatenated_text, all_files)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def is_included_file(self, filepath):
        # Check by extension
        _, ext = os.path.splitext(filepath)
        return ext.lower() in self.text_file_extensions

    def is_git_tracked(self, filepath):
        try:
            # Get the repository root
            repo_root = self.get_git_repo_root(filepath)
            if not repo_root:
                return False
            # Get the relative path to the repo root
            rel_path = os.path.relpath(filepath, repo_root)
            # Check if the file is tracked
            result = subprocess.run(['git', 'ls-files', '--error-unmatch', rel_path],
                                    cwd=repo_root,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True)
            return result.returncode == 0
        except Exception:
            return False

    def get_git_repo_root(self, filepath):
        try:
            result = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                                    cwd=os.path.dirname(filepath),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return None
        except Exception:
            return None

class ConcatenatorApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Text File Concatenator")
        self.setGeometry(100, 100, 1200, 800)
        self.selected_directory = ""
        self.output_file_path = os.path.join(os.path.expanduser("~"), "concatenated_output.txt")  # Default save location
        self.ignore_patterns = ['node_modules', 'venv', '.git', '__pycache__', 'dist', 'build', 'env', '.idea', '.vscode']
        self.default_file_extensions = [
            '.txt', '.md', '.py', '.js', '.java', '.cpp', '.c', '.cs', '.html', '.css',
            '.json', '.xml', '.rb', '.go', '.ts', '.swift', '.php', '.sh', '.bat', '.pl'
        ]
        self.text_file_extensions = set(ext.lower() for ext in self.default_file_extensions)  # Initialize here

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Select Directory Button
        dir_layout = QHBoxLayout()
        self.select_dir_button = QPushButton("Select Root Directory")
        self.select_dir_button.clicked.connect(self.select_directory)
        dir_layout.addWidget(self.select_dir_button)

        self.selected_dir_label = QLabel(f"Default Save Location: {self.output_file_path}")
        self.selected_dir_label.setWordWrap(True)
        dir_layout.addWidget(self.selected_dir_label)

        layout.addLayout(dir_layout)

        # File/Folder Browser as TreeWidget
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Name", "Path"])
        self.tree_widget.setColumnHidden(1, True)  # Hide the Path column
        self.tree_widget.setColumnWidth(0, 800)
        self.tree_widget.setSelectionMode(QTreeWidget.NoSelection)
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.itemChanged.connect(self.handle_item_changed)
        layout.addWidget(self.tree_widget)

        # Preferences Section
        preferences_group = QGroupBox("Preferences")
        preferences_layout = QVBoxLayout()

        # File Tracking Options
        tracking_layout = QHBoxLayout()
        self.all_files_radio = QRadioButton("All Files")
        self.git_tracked_radio = QRadioButton("Git Tracked Files Only")
        self.all_files_radio.setChecked(True)
        self.tracking_group = QButtonGroup()
        self.tracking_group.addButton(self.all_files_radio)
        self.tracking_group.addButton(self.git_tracked_radio)
        tracking_layout.addWidget(self.all_files_radio)
        tracking_layout.addWidget(self.git_tracked_radio)
        tracking_layout.addStretch()  # Push options to the left
        preferences_layout.addLayout(tracking_layout)

        # Ignore Patterns
        ignore_layout = QVBoxLayout()
        ignore_label = QLabel("Automatically Ignore Directories:")
        ignore_layout.addWidget(ignore_label)

        # Scroll Area for Ignore Checkboxes (Condensed)
        scroll_ignore = QScrollArea()
        scroll_ignore.setWidgetResizable(True)
        ignore_widget = QWidget()
        self.ignore_checkboxes = []
        ignore_grid = QGridLayout()
        # Arrange checkboxes in 3 columns for compactness
        columns = 3
        for i, pattern in enumerate(self.ignore_patterns):
            checkbox = QCheckBox(pattern)
            checkbox.setChecked(True)
            self.ignore_checkboxes.append(checkbox)
            row = i // columns
            col = i % columns
            ignore_grid.addWidget(checkbox, row, col)
        ignore_widget.setLayout(ignore_grid)
        scroll_ignore.setWidget(ignore_widget)
        scroll_ignore.setFixedHeight(100)  # Reduce height
        ignore_layout.addWidget(scroll_ignore)

        preferences_layout.addLayout(ignore_layout)

        # File Types Include/Exclude
        filetype_layout = QVBoxLayout()
        filetype_label = QLabel("File Types to Include:")
        filetype_layout.addWidget(filetype_label)

        # Scroll Area for File Type Checkboxes (Condensed)
        scroll_filetypes = QScrollArea()
        scroll_filetypes.setWidgetResizable(True)
        filetype_widget = QWidget()
        self.filetype_checkboxes = []
        filetype_grid = QGridLayout()
        # Arrange checkboxes in 4 columns for compactness
        columns = 4
        for i, ext in enumerate(self.default_file_extensions):
            checkbox = QCheckBox(ext)
            checkbox.setChecked(True)
            self.filetype_checkboxes.append(checkbox)
            row = i // columns
            col = i % columns
            filetype_grid.addWidget(checkbox, row, col)
        filetype_widget.setLayout(filetype_grid)
        scroll_filetypes.setWidget(filetype_widget)
        scroll_filetypes.setFixedHeight(150)  # Adjusted height for more filetypes
        filetype_layout.addWidget(scroll_filetypes)

        # Add an option to add custom file extensions
        custom_filetype_layout = QHBoxLayout()
        self.custom_filetype_input = QLineEdit()
        self.custom_filetype_input.setPlaceholderText("Add custom file extension (e.g., .ini)")
        self.add_filetype_button = QPushButton("Add")
        self.add_filetype_button.clicked.connect(self.add_custom_filetype)
        custom_filetype_layout.addWidget(self.custom_filetype_input)
        custom_filetype_layout.addWidget(self.add_filetype_button)
        filetype_layout.addLayout(custom_filetype_layout)

        preferences_layout.addLayout(filetype_layout)

        preferences_group.setLayout(preferences_layout)
        layout.addWidget(preferences_group)

        # Output Options
        output_layout = QHBoxLayout()

        # Radio Buttons for Output Options
        self.save_to_file_radio = QRadioButton("Save to File")
        self.copy_to_clipboard_radio = QRadioButton("Copy to Clipboard")
        self.save_to_file_radio.setChecked(True)
        self.output_option_group = QButtonGroup()
        self.output_option_group.addButton(self.save_to_file_radio)
        self.output_option_group.addButton(self.copy_to_clipboard_radio)
        output_layout.addWidget(self.save_to_file_radio)
        output_layout.addWidget(self.copy_to_clipboard_radio)
        output_layout.addStretch()  # Push options to the left

        layout.addLayout(output_layout)

        # Select Save Location
        self.save_layout = QHBoxLayout()
        self.select_save_button = QPushButton("Select Save Location")
        self.select_save_button.clicked.connect(self.select_save_location)
        self.save_layout.addWidget(self.select_save_button)

        self.save_path_edit = QLineEdit()
        self.save_path_edit.setReadOnly(True)
        self.save_path_edit.setText(self.output_file_path)  # Set default save path
        self.save_layout.addWidget(self.save_path_edit)

        layout.addLayout(self.save_layout)

        # Generate Button
        self.generate_button = QPushButton("Generate")
        self.generate_button.clicked.connect(self.generate_concatenation)
        layout.addWidget(self.generate_button)

        # Progress Bar and Status Label
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Status: Idle")
        progress_layout.addWidget(self.status_label)

        layout.addLayout(progress_layout)

        self.setLayout(layout)

        # Connect radio buttons to toggle save location selection
        self.save_to_file_radio.toggled.connect(self.toggle_save_location)

        # Initialize text_file_extensions based on default checkboxes
        self.update_text_file_extensions()

    def add_custom_filetype(self):
        ext = self.custom_filetype_input.text().strip().lower()
        if not ext.startswith('.'):
            QMessageBox.warning(self, "Invalid Extension", "File extension should start with a dot (e.g., .ini)")
            return
        if ext in [cb.text().lower() for cb in self.filetype_checkboxes]:
            QMessageBox.warning(self, "Duplicate Extension", f"The extension {ext} is already in the list.")
            return
        # Add new checkbox
        checkbox = QCheckBox(ext)
        checkbox.setChecked(True)
        self.filetype_checkboxes.append(checkbox)
        # Add to the grid layout
        filetype_grid = self.filetype_checkboxes[0].parentWidget().layout()
        row = len(self.filetype_checkboxes) // 4
        col = len(self.filetype_checkboxes) % 4
        filetype_grid.addWidget(checkbox, row, col)
        self.text_file_extensions.add(ext)
        self.custom_filetype_input.clear()

    def toggle_save_location(self):
        if self.save_to_file_radio.isChecked():
            self.select_save_button.setEnabled(True)
            self.save_path_edit.setEnabled(True)
            # If the output_file_path is default, set it
            if not self.save_path_edit.text():
                self.output_file_path = os.path.join(os.path.expanduser("~"), "concatenated_output.txt")
                self.save_path_edit.setText(self.output_file_path)
        else:
            self.select_save_button.setEnabled(False)
            self.save_path_edit.setEnabled(False)
            self.save_path_edit.clear()

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Root Directory", "")
        if directory:
            self.selected_directory = directory
            self.selected_dir_label.setText(f"Selected Directory: {directory}")
            self.populate_tree(directory)

    def populate_tree(self, directory):
        self.tree_widget.clear()
        try:
            root_item = QTreeWidgetItem(self.tree_widget, [os.path.basename(directory), directory])
            root_item.setFlags(root_item.flags() | Qt.ItemIsUserCheckable)
            root_item.setCheckState(0, Qt.Unchecked)
            self.add_children(root_item, directory)
            root_item.setExpanded(True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to populate tree: {str(e)}")

    def add_children(self, parent_item, parent_path):
        try:
            for name in os.listdir(parent_path):
                path = os.path.join(parent_path, name)
                # Skip ignored directories
                if os.path.isdir(path):
                    if name in self.ignore_patterns:
                        index = self.ignore_patterns.index(name)
                        if self.ignore_checkboxes[index].isChecked():
                            continue
                # Check file types if it's a file
                if os.path.isfile(path):
                    _, ext = os.path.splitext(name)
                    if ext.lower() not in self.text_file_extensions:
                        continue
                child_item = QTreeWidgetItem(parent_item, [name, path])
                child_item.setFlags(child_item.flags() | Qt.ItemIsUserCheckable)
                child_item.setCheckState(0, Qt.Unchecked)
                if os.path.isdir(path):
                    # Add a dummy child to make the item expandable
                    dummy = QTreeWidgetItem(child_item, ["Loading..."])
        except PermissionError:
            pass  # Skip directories for which the user does not have permissions

    def handle_item_changed(self, item, column):
        if item.childCount() > 0 and item.child(0).text(0) == "Loading...":
            # Populate children when expanding
            item.takeChildren()  # Remove dummy
            self.add_children(item, item.text(1))
        state = item.checkState(0)
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)

    def select_save_location(self):
        options = QFileDialog.Options()
        # options |= QFileDialog.DontUseNativeDialog  # Optional: Use native dialogs
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output File",
            self.output_file_path,  # Start at default save location
            "All Files (*)",
            options=options
        )
        if file_path:
            self.output_file_path = file_path
            self.save_path_edit.setText(file_path)

    def generate_concatenation(self):
        selected_paths = self.get_selected_paths()
        if not selected_paths:
            QMessageBox.warning(self, "No Selection", "Please select at least one file or folder to concatenate.")
            return

        if self.save_to_file_radio.isChecked() and not self.output_file_path:
            QMessageBox.warning(self, "No Output File", "Please specify an output file location.")
            return

        copy_to_clipboard = self.copy_to_clipboard_radio.isChecked()
        git_tracked = self.git_tracked_radio.isChecked()

        # Gather ignore patterns from checkboxes
        current_ignore_patterns = [cb.text() for cb in self.ignore_checkboxes if cb.isChecked()]

        # Gather include file extensions from checkboxes
        self.text_file_extensions = set(cb.text().lower() for cb in self.filetype_checkboxes if cb.isChecked())
        if not self.text_file_extensions:
            QMessageBox.warning(self, "No File Types Selected", "Please select at least one file type to include.")
            return

        # Disable UI elements during processing
        self.toggle_ui(False)

        # Reset progress bar and status
        self.progress_bar.setValue(0)
        self.status_label.setText("Status: Starting...")

        # Start the concatenation in a separate thread
        self.thread = FileConcatenatorThread(
            selected_paths,
            git_tracked=git_tracked,
            ignore_patterns=current_ignore_patterns,
            include_extensions=self.text_file_extensions
        )
        self.thread.progress_update.connect(self.update_progress)
        self.thread.status_update.connect(self.update_status)
        self.thread.error_occurred.connect(self.handle_error)
        self.thread.finished_successfully.connect(lambda text, files: self.concatenation_finished(text, files, copy_to_clipboard))
        self.thread.start()

    def get_selected_paths(self):
        selected = []
        root = self.tree_widget.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            self.collect_checked(child, selected)
        return selected

    def collect_checked(self, item, selected):
        if item.checkState(0) == Qt.Checked:
            selected.append(item.text(1))
        for i in range(item.childCount()):
            child = item.child(i)
            self.collect_checked(child, selected)

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def update_status(self, message):
        self.status_label.setText(f"Status: {message}")

    def handle_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.toggle_ui(True)
        self.status_label.setText("Status: Error occurred.")

    def concatenation_finished(self, concatenated_text, all_files, copy_to_clipboard):
        # Generate file tree string
        file_tree = self.generate_file_tree(all_files)

        # Combine file tree and concatenated text
        final_output = f"Output File Tree:\n{file_tree}\n\nConcatenated Contents:\n{concatenated_text}"

        # Calculate tokens and length
        word_count = len(concatenated_text.split())
        char_count = len(concatenated_text)
        total_length = len(final_output)

        if copy_to_clipboard:
            try:
                clipboard = QApplication.clipboard()
                clipboard.setText(final_output)
                QMessageBox.information(
                    self,
                    "Success",
                    f"Text has been copied to the clipboard successfully.\n\n"
                    f"Words: {word_count}\nCharacters: {char_count}\nTotal Length: {total_length} characters."
                )
            except Exception as e:
                QMessageBox.critical(self, "Clipboard Error", f"Failed to copy to clipboard: {str(e)}")
        else:
            try:
                with open(self.output_file_path, 'w', encoding='utf-8') as outfile:
                    outfile.write(final_output)
                QMessageBox.information(
                    self,
                    "Success",
                    f"Files have been concatenated successfully.\nSaved to {self.output_file_path}\n\n"
                    f"Words: {word_count}\nCharacters: {char_count}\nTotal Length: {total_length} characters."
                )
            except Exception as e:
                QMessageBox.critical(self, "Save Error", f"Failed to save file: {str(e)}")

        self.toggle_ui(True)
        self.status_label.setText("Status: Completed.")

    def generate_file_tree(self, files):
        # Build a nested dictionary to represent the tree
        tree = {}
        for file_path in files:
            parts = os.path.relpath(file_path, self.selected_directory).split(os.sep)
            current_level = tree
            for part in parts[:-1]:
                current_level = current_level.setdefault(part, {})
            current_level[parts[-1]] = None  # File

        # Convert the nested dictionary to a string with indentation
        def traverse(d, indent=0):
            tree_str = ""
            for key in sorted(d.keys()):
                tree_str += "    " * indent + f"- {key}\n"
                if isinstance(d[key], dict):
                    tree_str += traverse(d[key], indent + 1)
            return tree_str

        return traverse(tree)

    def toggle_ui(self, enabled):
        self.select_dir_button.setEnabled(enabled)
        self.tree_widget.setEnabled(enabled)
        self.select_save_button.setEnabled(enabled and self.save_to_file_radio.isChecked())
        self.generate_button.setEnabled(enabled)
        self.save_to_file_radio.setEnabled(enabled)
        self.copy_to_clipboard_radio.setEnabled(enabled)
        self.git_tracked_radio.setEnabled(enabled)
        self.all_files_radio.setEnabled(enabled)
        for cb in self.ignore_checkboxes:
            cb.setEnabled(enabled)
        for cb in self.filetype_checkboxes:
            cb.setEnabled(enabled)
        self.add_filetype_button_state(enabled)

    def add_filetype_button_state(self, enabled):
        self.custom_filetype_input.setEnabled(enabled)
        self.add_filetype_button.setEnabled(enabled)

    def closeEvent(self, event):
        try:
            if hasattr(self, 'thread') and self.thread.isRunning():
                self.thread.terminate()
        except:
            pass
        event.accept()

    def update_text_file_extensions(self):
        # Initialize text_file_extensions based on default checkboxes
        self.text_file_extensions = set(cb.text().lower() for cb in self.filetype_checkboxes if cb.isChecked())

def main():
    app = QApplication(sys.argv)
    window = ConcatenatorApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

