
import sys
import os
import mimetypes
import subprocess
import json
import fnmatch
import logging
from collections import defaultdict
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog,
    QLabel, QTreeWidget, QTreeWidgetItem, QHBoxLayout, QLineEdit,
    QProgressBar, QMessageBox, QRadioButton, QButtonGroup, QCheckBox,
    QGroupBox, QScrollArea, QGridLayout, QSizePolicy, QSpacerItem,
    QTabWidget, QTextEdit, QListWidget, QListWidgetItem, QSplitter, QInputDialog, QAction
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QClipboard, QIcon, QKeySequence

# Configure logging
logging.basicConfig(
    filename='concatenator.log',
    filemode='w',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)

CONFIG_FILE = 'config.json'

class FileConcatenatorThread(QThread):
    progress_update = pyqtSignal(int, str)  # Emit progress percent and current file
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    finished_successfully = pyqtSignal(str, list, list)  # Emit concatenated text, list of files, error list

    def __init__(self, selected_paths, git_tracked=False, directory_ignore_patterns=None, file_ignore_patterns=None, include_extensions=None):
        super().__init__()
        self.selected_paths = selected_paths
        self.git_tracked = git_tracked
        self.directory_ignore_patterns = directory_ignore_patterns if directory_ignore_patterns else []
        self.file_ignore_patterns = file_ignore_patterns if file_ignore_patterns else []
        self.include_extensions = set(ext.lower() for ext in include_extensions) if include_extensions else set()
        self.text_file_extensions = self.include_extensions  # Alias for clarity
        self._is_cancelled = False

        # Caching for Git repositories and tracked files
        self.repo_roots = {}  # Cache for repository roots
        self.tracked_files_cache = defaultdict(set)  # Cache for tracked files per repo

    def run(self):
        logging.info("Concatenation thread started.")
        try:
            # Gather all relevant files from selected paths
            all_files = []
            for path in self.selected_paths:
                if self._is_cancelled:
                    self.status_update.emit("Operation cancelled by user.")
                    logging.info("Operation cancelled by user.")
                    return
                if os.path.isfile(path):
                    if self.is_included_file(path) and not self.is_ignored_file(path):
                        if self.git_tracked:
                            if self.is_git_tracked(path):
                                all_files.append(path)
                        else:
                            all_files.append(path)
                elif os.path.isdir(path):
                    for root, dirs, files in os.walk(path, topdown=True):
                        if self._is_cancelled:
                            self.status_update.emit("Operation cancelled by user.")
                            logging.info("Operation cancelled by user.")
                            return
                        # Modify dirs in-place to skip ignored directories
                        dirs[:] = [d for d in dirs if d not in self.directory_ignore_patterns]
                        for file in files:
                            if self._is_cancelled:
                                self.status_update.emit("Operation cancelled by user.")
                                logging.info("Operation cancelled by user.")
                                return
                            file_path = os.path.join(root, file)
                            if self.is_included_file(file_path) and not self.is_ignored_file(file_path):
                                if self.git_tracked:
                                    if self.is_git_tracked(file_path):
                                        all_files.append(file_path)
                                else:
                                    all_files.append(file_path)

            total_files = len(all_files)
            logging.info(f"Total files to process: {total_files}")
            if total_files == 0:
                error_message = "No files found based on the selected preferences."
                self.error_occurred.emit(error_message)
                logging.warning(error_message)
                return

            self.status_update.emit("Starting concatenation...")
            concatenated_text = ""
            error_list = []
            for index, file_path in enumerate(all_files, start=1):
                if self._is_cancelled:
                    self.status_update.emit("Operation cancelled by user.")
                    logging.info("Operation cancelled by user.")
                    return
                try:
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        content = infile.read()
                        # Prepend file name as a header
                        header = f"=== {os.path.basename(file_path)} ===\n"
                        concatenated_text += header + content + '\n\n'  # Separator between files
                except Exception as e:
                    error_message = f"Error reading {file_path}: {str(e)}"
                    error_list.append(error_message)
                    logging.error(error_message)
                    continue  # Continue processing other files
                progress_percent = int((index / total_files) * 100)
                self.progress_update.emit(progress_percent, os.path.basename(file_path))
                self.status_update.emit(f"Processing {os.path.basename(file_path)} ({index}/{total_files})")
                logging.debug(f"Processed file: {file_path} ({index}/{total_files})")

            self.status_update.emit("Concatenation completed successfully.")
            logging.info("Concatenation completed successfully.")
            self.finished_successfully.emit(concatenated_text, all_files, error_list)
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            self.error_occurred.emit(error_msg)
            logging.exception("An unexpected error occurred in the concatenation thread.")

    def cancel(self):
        self._is_cancelled = True
        logging.info("Cancellation requested by user.")

    def is_included_file(self, filepath):
        # Check by extension
        _, ext = os.path.splitext(filepath)
        return ext.lower() in self.text_file_extensions

    def is_ignored_file(self, filepath):
        # Check if file matches any ignore pattern
        filename = os.path.basename(filepath)
        for pattern in self.file_ignore_patterns:
            if fnmatch.fnmatch(filename, pattern):
                logging.debug(f"Ignored file due to pattern: {filepath}")
                return True
        return False

    def is_git_tracked(self, filepath):
        try:
            repo_root = self.get_git_repo_root(filepath)
            if not repo_root:
                logging.debug(f"No Git repository found for file: {filepath}")
                return False

            # Check if tracked files are already cached for this repo
            if repo_root in self.tracked_files_cache:
                rel_path = os.path.relpath(filepath, repo_root)
                is_tracked = rel_path in self.tracked_files_cache[repo_root]
                logging.debug(f"Cache hit for {filepath}: {'Tracked' if is_tracked else 'Untracked'}")
                return is_tracked

            # If not cached, retrieve all tracked files for the repo
            creationflags = 0
            if sys.platform.startswith('win'):
                creationflags = subprocess.CREATE_NO_WINDOW

            try:
                result = subprocess.run(
                    ['git', 'ls-files'],
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=creationflags,
                    timeout=10  # 10 seconds timeout
                )
            except subprocess.TimeoutExpired:
                error_message = f"Git command timed out for repository: {repo_root}"
                self.error_occurred.emit(error_message)
                logging.error(error_message)
                return False

            if result.returncode == 0:
                tracked_files = set(result.stdout.strip().split('\n'))
                self.tracked_files_cache[repo_root] = tracked_files
                rel_path = os.path.relpath(filepath, repo_root)
                is_tracked = rel_path in tracked_files
                logging.debug(f"File {filepath} is {'tracked' if is_tracked else 'untracked'} in Git repository.")
                return is_tracked
            else:
                error_message = f"Git error in repository {repo_root}: {result.stderr.strip()}"
                self.error_occurred.emit(error_message)
                logging.error(error_message)
                return False
        except Exception as e:
            error_message = f"Error checking Git status for {filepath}: {str(e)}"
            self.error_occurred.emit(error_message)
            logging.exception(error_message)
            return False

    def get_git_repo_root(self, filepath):
        if filepath in self.repo_roots:
            logging.debug(f"Repo root for {filepath} fetched from cache.")
            return self.repo_roots[filepath]

        try:
            creationflags = 0
            if sys.platform.startswith('win'):
                creationflags = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(
                ['git', 'rev-parse', '--show-toplevel'],
                cwd=os.path.dirname(filepath),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
                timeout=10  # 10 seconds timeout
            )
        except subprocess.TimeoutExpired:
            error_message = f"Git command timed out while determining repo root for file: {filepath}"
            self.error_occurred.emit(error_message)
            logging.error(error_message)
            return None

        if result.returncode == 0:
            repo_root = result.stdout.strip()
            self.repo_roots[filepath] = repo_root
            logging.debug(f"Found Git repository root for {filepath}: {repo_root}")
            return repo_root
        else:
            logging.debug(f"No Git repository found for {filepath}: {result.stderr.strip()}")
            return None

class ConcatenatorApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Text File Concatenator")
        self.setGeometry(100, 100, 1400, 1000)
        self.selected_directory = ""
        self.output_file_path = os.path.join(os.path.expanduser("~"), "concatenated_output.txt")  # Default save location
        self.directory_ignore_patterns = ['node_modules', 'venv', '.git', '__pycache__', 'dist', 'build', 'env', '.idea', '.vscode']
        self.file_ignore_patterns = []  # Initialize file ignore patterns
        self.default_file_extensions = [
            '.txt', '.md', '.py', '.js', '.java', '.cpp', '.c', '.cs', '.html', '.css',
            '.json', '.xml', '.rb', '.go', '.ts', '.swift', '.php', '.sh', '.bat', '.pl'
        ]
        self.text_file_extensions = set(ext.lower() for ext in self.default_file_extensions)  # Initialize here

        self.load_config()

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout()

        # Create Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Selection Tab
        self.selection_tab = QWidget()
        self.init_selection_tab()
        self.tabs.addTab(self.selection_tab, "Selection")

        # Preferences Tab
        self.preferences_tab = QWidget()
        self.init_preferences_tab()
        self.tabs.addTab(self.preferences_tab, "Preferences")

        # Output Tab
        self.output_tab = QWidget()
        self.init_output_tab()
        self.tabs.addTab(self.output_tab, "Output")

        # Generate Button and Status
        bottom_layout = QHBoxLayout()
        self.generate_button = QPushButton("Generate (Ctrl+G)")
        self.generate_button.setShortcut(QKeySequence("Ctrl+G"))
        self.generate_button.clicked.connect(self.generate_concatenation)
        self.generate_button.setToolTip("Start the concatenation process")
        bottom_layout.addWidget(self.generate_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_concatenation)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Cancel the ongoing concatenation")
        bottom_layout.addWidget(self.cancel_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Status: Idle")
        bottom_layout.addWidget(self.status_label)

        main_layout.addLayout(bottom_layout)

        # Error Log
        error_layout = QVBoxLayout()
        error_label = QLabel("Error Log:")
        error_layout.addWidget(error_label)
        self.error_log = QTextEdit()
        self.error_log.setReadOnly(True)
        error_layout.addWidget(self.error_log)
        main_layout.addLayout(error_layout)

        self.setLayout(main_layout)

        # Keyboard Shortcuts
        open_dir_action = QAction(self)
        open_dir_action.setShortcut(QKeySequence("Ctrl+O"))
        open_dir_action.triggered.connect(self.select_directory)
        self.addAction(open_dir_action)

        save_output_action = QAction(self)
        save_output_action.setShortcut(QKeySequence("Ctrl+S"))
        save_output_action.triggered.connect(self.select_save_location)
        self.addAction(save_output_action)

        # Initialize text_file_extensions based on default checkboxes
        self.update_text_file_extensions()

    def init_selection_tab(self):
        layout = QVBoxLayout()

        # Select Directory Button and Search Bar
        top_layout = QHBoxLayout()
        self.select_dir_button = QPushButton("Select Root Directory (Ctrl+O)")
        self.select_dir_button.setShortcut(QKeySequence("Ctrl+O"))
        self.select_dir_button.clicked.connect(self.select_directory)
        self.select_dir_button.setToolTip("Choose the root directory for file selection")
        top_layout.addWidget(self.select_dir_button)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search files and folders...")
        self.search_bar.textChanged.connect(self.filter_tree)
        self.search_bar.setToolTip("Search for specific files or folders in the tree")
        top_layout.addWidget(self.search_bar)

        layout.addLayout(top_layout)

        # Select All / Deselect All Buttons
        select_buttons_layout = QHBoxLayout()
        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(self.select_all_items)
        self.select_all_button.setToolTip("Select all files and folders")
        select_buttons_layout.addWidget(self.select_all_button)

        self.deselect_all_button = QPushButton("Deselect All")
        self.deselect_all_button.clicked.connect(self.deselect_all_items)
        self.deselect_all_button.setToolTip("Deselect all files and folders")
        select_buttons_layout.addWidget(self.deselect_all_button)

        layout.addLayout(select_buttons_layout)

        # File/Folder Browser as TreeWidget with Lazy Loading
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Name", "Path"])
        self.tree_widget.setColumnHidden(1, True)  # Hide the Path column
        self.tree_widget.setColumnWidth(0, 800)
        self.tree_widget.setSelectionMode(QTreeWidget.NoSelection)
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.itemChanged.connect(self.handle_item_changed)
        self.tree_widget.itemExpanded.connect(self.handle_item_expanded)
        layout.addWidget(self.tree_widget)

        self.selection_tab.setLayout(layout)

    def init_preferences_tab(self):
        layout = QVBoxLayout()

        # File Tracking Options
        tracking_group_box = QGroupBox("File Tracking Options")
        tracking_layout = QHBoxLayout()
        self.all_files_radio = QRadioButton("All Files")
        self.git_tracked_radio = QRadioButton("Git Tracked Files Only")
        self.all_files_radio.setChecked(True)
        self.tracking_group = QButtonGroup()
        self.tracking_group.addButton(self.all_files_radio)
        self.tracking_group.addButton(self.git_tracked_radio)
        tracking_layout.addWidget(self.all_files_radio)
        tracking_layout.addWidget(self.git_tracked_radio)
        tracking_layout.addStretch()
        tracking_group_box.setLayout(tracking_layout)
        layout.addWidget(tracking_group_box)

        # Ignore Directories Management
        ignore_dir_group_box = QGroupBox("Ignore Directories")
        ignore_dir_layout = QVBoxLayout()

        # Current Ignore Patterns List
        self.ignore_dir_list = QListWidget()
        for pattern in self.directory_ignore_patterns:
            item = QListWidgetItem(pattern)
            self.ignore_dir_list.addItem(item)
        ignore_dir_layout.addWidget(self.ignore_dir_list)

        # Add/Remove Buttons
        ignore_dir_buttons_layout = QHBoxLayout()
        self.add_ignore_dir_button = QPushButton("Add Directory")
        self.add_ignore_dir_button.clicked.connect(self.add_ignore_directory)
        self.add_ignore_dir_button.setToolTip("Add a new directory to ignore")
        ignore_dir_buttons_layout.addWidget(self.add_ignore_dir_button)

        self.remove_ignore_dir_button = QPushButton("Remove Selected")
        self.remove_ignore_dir_button.clicked.connect(self.remove_ignore_directory)
        self.remove_ignore_dir_button.setToolTip("Remove selected directory from ignore list")
        ignore_dir_buttons_layout.addWidget(self.remove_ignore_dir_button)

        ignore_dir_layout.addLayout(ignore_dir_buttons_layout)
        ignore_dir_group_box.setLayout(ignore_dir_layout)
        layout.addWidget(ignore_dir_group_box)

        # Ignore File Patterns Management
        ignore_file_group_box = QGroupBox("Ignore File Patterns")
        ignore_file_layout = QVBoxLayout()

        # Current File Ignore Patterns List
        self.ignore_file_list = QListWidget()
        for pattern in self.file_ignore_patterns:
            item = QListWidgetItem(pattern)
            self.ignore_file_list.addItem(item)
        ignore_file_layout.addWidget(self.ignore_file_list)

        # Add/Remove Buttons
        ignore_file_buttons_layout = QHBoxLayout()
        self.add_ignore_file_button = QPushButton("Add Pattern")
        self.add_ignore_file_button.clicked.connect(self.add_ignore_file_pattern)
        self.add_ignore_file_button.setToolTip("Add a new file pattern to ignore (e.g., *.test.js)")
        ignore_file_buttons_layout.addWidget(self.add_ignore_file_button)

        self.remove_ignore_file_button = QPushButton("Remove Selected")
        self.remove_ignore_file_button.clicked.connect(self.remove_ignore_file_pattern)
        self.remove_ignore_file_button.setToolTip("Remove selected file pattern from ignore list")
        ignore_file_buttons_layout.addWidget(self.remove_ignore_file_button)

        ignore_file_layout.addLayout(ignore_file_buttons_layout)
        ignore_file_group_box.setLayout(ignore_file_layout)
        layout.addWidget(ignore_file_group_box)

        # File Types Management
        filetype_group_box = QGroupBox("File Types to Include")
        filetype_layout = QHBoxLayout()

        # Available File Types
        available_layout = QVBoxLayout()
        available_label = QLabel("Available:")
        available_layout.addWidget(available_label)
        self.available_filetypes = QListWidget()
        for ext in self.default_file_extensions:
            item = QListWidgetItem(ext)
            item.setCheckState(Qt.Checked)
            self.available_filetypes.addItem(item)
        filetype_layout.addLayout(available_layout)
        filetype_layout.addWidget(self.available_filetypes)

        # Selected File Types
        selected_layout = QVBoxLayout()
        selected_label = QLabel("Selected:")
        selected_layout.addWidget(selected_label)
        self.selected_filetypes = QListWidget()
        for ext in self.text_file_extensions:
            item = QListWidgetItem(ext)
            item.setCheckState(Qt.Checked)
            self.selected_filetypes.addItem(item)
        filetype_layout.addLayout(selected_layout)
        filetype_layout.addWidget(self.selected_filetypes)

        # Add/Remove Buttons
        filetype_buttons_layout = QVBoxLayout()
        self.add_filetype_button = QPushButton("Add →")
        self.add_filetype_button.clicked.connect(self.move_filetype_to_selected)
        self.add_filetype_button.setToolTip("Add selected file types to include list")
        filetype_buttons_layout.addWidget(self.add_filetype_button)

        self.remove_filetype_button = QPushButton("← Remove")
        self.remove_filetype_button.clicked.connect(self.move_filetype_to_available)
        self.remove_filetype_button.setToolTip("Remove selected file types from include list")
        filetype_buttons_layout.addWidget(self.remove_filetype_button)

        filetype_layout.addLayout(filetype_buttons_layout)

        # Add File Types Layout to Main Filetype Layout
        filetype_layout.addStretch()

        # Custom File Type Input
        custom_filetype_layout = QHBoxLayout()
        self.custom_filetype_input = QLineEdit()
        self.custom_filetype_input.setPlaceholderText("Add custom file extension (e.g., .ini)")
        self.custom_filetype_input.setToolTip("Enter a custom file extension to include")
        custom_filetype_layout.addWidget(self.custom_filetype_input)
        self.add_custom_filetype_button = QPushButton("Add Custom")
        self.add_custom_filetype_button.clicked.connect(self.add_custom_filetype)
        self.add_custom_filetype_button.setToolTip("Add the custom file extension to the selected list")
        custom_filetype_layout.addWidget(self.add_custom_filetype_button)
        filetype_layout.addLayout(custom_filetype_layout)

        filetype_group_box.setLayout(filetype_layout)
        layout.addWidget(filetype_group_box)

        # Expand File Types Management
        layout.addStretch()

        self.preferences_tab.setLayout(layout)

    def init_output_tab(self):
        layout = QVBoxLayout()

        # Output Options
        output_options_group_box = QGroupBox("Output Options")
        output_layout = QHBoxLayout()
        self.save_to_file_radio = QRadioButton("Save to File")
        self.copy_to_clipboard_radio = QRadioButton("Copy to Clipboard")
        self.save_to_file_radio.setChecked(True)
        self.output_option_group = QButtonGroup()
        self.output_option_group.addButton(self.save_to_file_radio)
        self.output_option_group.addButton(self.copy_to_clipboard_radio)
        output_layout.addWidget(self.save_to_file_radio)
        output_layout.addWidget(self.copy_to_clipboard_radio)
        output_layout.addStretch()
        output_options_group_box.setLayout(output_layout)
        layout.addWidget(output_options_group_box)

        # Simultaneous Save and Copy
        self.simultaneous_checkbox = QCheckBox("Save to File and Copy to Clipboard")
        self.simultaneous_checkbox.setChecked(False)
        self.simultaneous_checkbox.setToolTip("Enable both saving to file and copying to clipboard")
        layout.addWidget(self.simultaneous_checkbox)

        # Select Save Location
        save_layout = QHBoxLayout()
        self.select_save_button = QPushButton("Select Save Location (Ctrl+S)")
        self.select_save_button.setShortcut(QKeySequence("Ctrl+S"))
        self.select_save_button.clicked.connect(self.select_save_location)
        self.select_save_button.setToolTip("Choose where to save the concatenated output file")
        save_layout.addWidget(self.select_save_button)

        self.save_path_edit = QLineEdit()
        self.save_path_edit.setReadOnly(True)
        self.save_path_edit.setText(self.output_file_path)  # Set default save path
        save_layout.addWidget(self.save_path_edit)

        layout.addLayout(save_layout)

        # Preview Pane
        preview_group_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout()
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        preview_layout.addWidget(self.preview_text)
        preview_group_box.setLayout(preview_layout)
        layout.addWidget(preview_group_box)

        self.output_tab.setLayout(layout)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.directory_ignore_patterns = config.get('directory_ignore_patterns', self.directory_ignore_patterns)
                    self.file_ignore_patterns = config.get('file_ignore_patterns', self.file_ignore_patterns)
                    custom_filetypes = config.get('custom_filetypes', [])
                    self.default_file_extensions.extend([ext for ext in custom_filetypes if ext not in self.default_file_extensions])
                    self.text_file_extensions.update(ext.lower() for ext in custom_filetypes)
                    logging.info("Configuration loaded successfully.")
            except Exception as e:
                logging.exception(f"Failed to load configuration: {str(e)}")

    def save_config(self):
        config = {
            'directory_ignore_patterns': [self.ignore_dir_list.item(i).text() for i in range(self.ignore_dir_list.count())],
            'file_ignore_patterns': [self.ignore_file_list.item(i).text() for i in range(self.ignore_file_list.count())],
            'custom_filetypes': [self.selected_filetypes.item(i).text() for i in range(self.selected_filetypes.count()) if self.selected_filetypes.item(i).checkState() == Qt.Checked and self.selected_filetypes.item(i).text() not in self.default_file_extensions]
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            logging.info("Configuration saved successfully.")
        except Exception as e:
            logging.exception(f"Failed to save configuration: {str(e)}")

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Root Directory", "")
        if directory:
            self.selected_directory = directory
            self.tabs.setCurrentWidget(self.selection_tab)
            self.populate_tree(directory)
            logging.info(f"Selected root directory: {directory}")

    def populate_tree(self, directory):
        self.tree_widget.clear()
        try:
            root_item = QTreeWidgetItem(self.tree_widget, [os.path.basename(directory), directory])
            root_item.setFlags(root_item.flags() | Qt.ItemIsUserCheckable)
            root_item.setCheckState(0, Qt.Unchecked)
            # Add a dummy child to make the item expandable
            dummy = QTreeWidgetItem(root_item, ["Loading..."])
            root_item.setExpanded(False)
            logging.debug(f"Populated tree with root directory: {directory}")
        except Exception as e:
            error_message = f"Failed to populate tree: {str(e)}"
            QMessageBox.critical(self, "Error", error_message)
            logging.exception(error_message)

    def add_children(self, parent_item, parent_path):
        try:
            for name in os.listdir(parent_path):
                path = os.path.join(parent_path, name)
                # Skip ignored directories
                if os.path.isdir(path):
                    if name in self.directory_ignore_patterns:
                        logging.debug(f"Skipping ignored directory: {path}")
                        continue
                # Check file types if it's a file
                if os.path.isfile(path):
                    _, ext = os.path.splitext(name)
                    if ext.lower() not in self.text_file_extensions:
                        logging.debug(f"Skipping file due to extension: {path}")
                        continue
                child_item = QTreeWidgetItem(parent_item, [name, path])
                child_item.setFlags(child_item.flags() | Qt.ItemIsUserCheckable)
                child_item.setCheckState(0, Qt.Unchecked)
                if os.path.isdir(path):
                    # Add a dummy child to make the item expandable
                    dummy = QTreeWidgetItem(child_item, ["Loading..."])
            logging.debug(f"Added children to {parent_path}")
        except PermissionError:
            logging.warning(f"Permission denied while accessing: {parent_path}")
            pass  # Skip directories for which the user does not have permissions
        except Exception as e:
            logging.exception(f"Error adding children to {parent_path}: {str(e)}")

    def handle_item_expanded(self, item):
        if item.childCount() == 1 and item.child(0).text(0) == "Loading...":
            item.takeChildren()  # Remove dummy
            self.add_children(item, item.text(1))

    def handle_item_changed(self, item, column):
        state = item.checkState(0)
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
        logging.debug(f"Item '{item.text(0)}' set to {'Checked' if state == Qt.Checked else 'Unchecked'}")

    def filter_tree(self, text):
        def recurse(item):
            match = text.lower() in item.text(0).lower()
            child_match = False
            for i in range(item.childCount()):
                child = item.child(i)
                child_visible = recurse(child)
                child_match = child_match or child_visible
            item.setHidden(not (match or child_match))
            return match or child_match

        root = self.tree_widget.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            recurse(child)
        logging.debug(f"Filtered tree with search text: '{text}'")

    def select_all_items(self):
        root = self.tree_widget.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            child.setCheckState(0, Qt.Checked)
        logging.info("All items selected.")

    def deselect_all_items(self):
        root = self.tree_widget.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            child.setCheckState(0, Qt.Unchecked)
        logging.info("All items deselected.")

    def add_ignore_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Add Ignore Directory", "")
        if directory:
            pattern = os.path.basename(directory)
            if pattern not in self.directory_ignore_patterns:
                self.directory_ignore_patterns.append(pattern)
                self.ignore_dir_list.addItem(pattern)
                logging.info(f"Added ignore directory pattern: {pattern}")
            else:
                QMessageBox.warning(self, "Duplicate Pattern", f"The directory '{pattern}' is already in the ignore list.")
                logging.warning(f"Attempted to add duplicate ignore directory pattern: {pattern}")

    def remove_ignore_directory(self):
        selected_items = self.ignore_dir_list.selectedItems()
        if not selected_items:
            return
        for item in selected_items:
            pattern = item.text()
            self.directory_ignore_patterns.remove(pattern)
            self.ignore_dir_list.takeItem(self.ignore_dir_list.row(item))
            logging.info(f"Removed ignore directory pattern: {pattern}")

    def add_ignore_file_pattern(self):
        pattern, ok = QInputDialog.getText(self, "Add File Ignore Pattern", "Enter file pattern to ignore (e.g., *.test.js):")
        if ok and pattern:
            pattern = pattern.strip()
            if not pattern:
                QMessageBox.warning(self, "Invalid Pattern", "File pattern cannot be empty.")
                logging.warning("Attempted to add an empty file ignore pattern.")
                return
            if pattern in self.file_ignore_patterns:
                QMessageBox.warning(self, "Duplicate Pattern", f"The pattern '{pattern}' is already in the ignore list.")
                logging.warning(f"Attempted to add duplicate file ignore pattern: {pattern}")
                return
            # Optionally, validate the pattern format here
            self.file_ignore_patterns.append(pattern)
            self.ignore_file_list.addItem(pattern)
            logging.info(f"Added file ignore pattern: {pattern}")

    def remove_ignore_file_pattern(self):
        selected_items = self.ignore_file_list.selectedItems()
        if not selected_items:
            return
        for item in selected_items:
            pattern = item.text()
            self.file_ignore_patterns.remove(pattern)
            self.ignore_file_list.takeItem(self.ignore_file_list.row(item))
            logging.info(f"Removed file ignore pattern: {pattern}")

    def move_filetype_to_selected(self):
        selected_items = self.available_filetypes.selectedItems()
        for item in selected_items:
            if item.text() not in [self.selected_filetypes.item(i).text() for i in range(self.selected_filetypes.count())]:
                new_item = QListWidgetItem(item.text())
                new_item.setCheckState(Qt.Checked)
                self.selected_filetypes.addItem(new_item)
                logging.info(f"Moved file type to selected: {item.text()}")

    def move_filetype_to_available(self):
        selected_items = self.selected_filetypes.selectedItems()
        for item in selected_items:
            self.selected_filetypes.takeItem(self.selected_filetypes.row(item))
            logging.info(f"Moved file type to available: {item.text()}")

    def add_custom_filetype(self):
        ext = self.custom_filetype_input.text().strip().lower()
        if not ext.startswith('.'):
            QMessageBox.warning(self, "Invalid Extension", "File extension should start with a dot (e.g., .ini)")
            logging.warning(f"Invalid custom file extension attempted to add: '{ext}'")
            return
        if ext in [self.available_filetypes.item(i).text().lower() for i in range(self.available_filetypes.count())] or \
           ext in [self.selected_filetypes.item(i).text().lower() for i in range(self.selected_filetypes.count())]:
            QMessageBox.warning(self, "Duplicate Extension", f"The extension {ext} is already in the list.")
            logging.warning(f"Attempted to add duplicate custom file extension: {ext}")
            return
        # Add to selected file types
        new_item = QListWidgetItem(ext)
        new_item.setCheckState(Qt.Checked)
        self.selected_filetypes.addItem(new_item)
        self.custom_filetype_input.clear()
        logging.info(f"Added custom file extension: {ext}")

    def select_save_location(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output File",
            self.output_file_path,  # Start at default save location
            "All Files (*)"
        )
        if file_path:
            self.output_file_path = file_path
            self.save_path_edit.setText(file_path)
            logging.info(f"Selected save location: {file_path}")

    def generate_concatenation(self):
        selected_paths = self.get_selected_paths()
        if not selected_paths:
            QMessageBox.warning(self, "No Selection", "Please select at least one file or folder to concatenate.")
            logging.warning("Generate clicked without any files selected.")
            return

        if not self.simultaneous_checkbox.isChecked() and self.save_to_file_radio.isChecked() and not self.output_file_path:
            QMessageBox.warning(self, "No Output File", "Please specify an output file location.")
            logging.warning("Generate clicked without specifying output file location.")
            return

        copy_to_clipboard = self.copy_to_clipboard_radio.isChecked() or self.simultaneous_checkbox.isChecked()
        save_to_file = self.save_to_file_radio.isChecked() or self.simultaneous_checkbox.isChecked()

        # Gather ignore patterns from lists
        current_directory_ignore_patterns = [self.ignore_dir_list.item(i).text() for i in range(self.ignore_dir_list.count())]
        current_file_ignore_patterns = [self.ignore_file_list.item(i).text() for i in range(self.ignore_file_list.count())]

        # Gather include file extensions from selected_filetypes
        self.text_file_extensions = set()
        for i in range(self.selected_filetypes.count()):
            item = self.selected_filetypes.item(i)
            if item.checkState() == Qt.Checked:
                self.text_file_extensions.add(item.text().lower())

        if not self.text_file_extensions and save_to_file:
            QMessageBox.warning(self, "No File Types Selected", "Please select at least one file type to include.")
            logging.warning("Generate clicked without any file types selected.")
            return

        # Disable UI elements during processing
        self.toggle_ui(False)

        # Reset progress bar and status
        self.progress_bar.setValue(0)
        self.status_label.setText("Status: Starting...")
        self.error_log.clear()
        self.preview_text.clear()

        # Start the concatenation in a separate thread
        self.thread = FileConcatenatorThread(
            selected_paths,
            git_tracked=self.git_tracked_radio.isChecked(),
            directory_ignore_patterns=current_directory_ignore_patterns,
            file_ignore_patterns=current_file_ignore_patterns,
            include_extensions=self.text_file_extensions
        )
        self.thread.progress_update.connect(self.update_progress)
        self.thread.status_update.connect(self.update_status)
        self.thread.error_occurred.connect(self.handle_error)
        self.thread.finished_successfully.connect(lambda text, files, errors: self.concatenation_finished(text, files, errors, copy_to_clipboard, save_to_file))
        self.thread.start()
        logging.info("Concatenation thread started.")

        # Enable cancel button
        self.cancel_button.setEnabled(True)

    def cancel_concatenation(self):
        if hasattr(self, 'thread') and self.thread.isRunning():
            self.thread.cancel()
            self.status_label.setText("Status: Cancelling...")
            self.cancel_button.setEnabled(False)
            logging.info("Cancellation initiated by user.")

    def get_selected_paths(self):
        selected = []
        root = self.tree_widget.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            self.collect_checked(child, selected)
        logging.debug(f"Selected paths for concatenation: {selected}")
        return selected

    def collect_checked(self, item, selected):
        if item.checkState(0) == Qt.Checked:
            selected.append(item.text(1))
        for i in range(item.childCount()):
            child = item.child(i)
            self.collect_checked(child, selected)

    def update_progress(self, value, current_file):
        self.progress_bar.setValue(value)
        self.status_label.setText(f"Processing: {current_file} ({value}%)")
        logging.debug(f"Progress updated: {current_file} ({value}%)")

    def update_status(self, message):
        self.status_label.setText(f"Status: {message}")
        logging.debug(f"Status updated: {message}")

    def handle_error(self, error_message):
        self.error_log.append(error_message)
        QMessageBox.critical(self, "Error", error_message)
        self.toggle_ui(True)
        self.status_label.setText("Status: Error occurred.")
        self.cancel_button.setEnabled(False)
        logging.error(f"Error occurred: {error_message}")

    def concatenation_finished(self, concatenated_text, all_files, error_list, copy_to_clipboard, save_to_file):
        # Generate file tree string
        file_tree = self.generate_file_tree(all_files)

        # Combine file tree and concatenated text
        final_output = f"Output File Tree:\n{file_tree}\n\nConcatenated Contents:\n{concatenated_text}"

        # Calculate tokens and length
        word_count = len(concatenated_text.split())
        char_count = len(concatenated_text)
        total_length = len(final_output)

        # Display preview (limit to first 1000 characters)
        preview_content = final_output[:1000] + ('...' if len(final_output) > 1000 else '')
        self.preview_text.setPlainText(preview_content)
        logging.debug("Preview updated with concatenated content.")

        # Handle errors
        if error_list:
            self.error_log.append("\nErrors Encountered:")
            for error in error_list:
                self.error_log.append(error)
            logging.warning(f"Concatenation completed with errors: {error_list}")

        # Handle output options
        if copy_to_clipboard:
            try:
                clipboard = QApplication.clipboard()
                clipboard.setText(final_output)
                logging.info("Concatenated text copied to clipboard.")
            except Exception as e:
                self.error_log.append(f"Failed to copy to clipboard: {str(e)}")
                QMessageBox.critical(self, "Clipboard Error", f"Failed to copy to clipboard: {str(e)}")
                logging.exception(f"Failed to copy to clipboard: {str(e)}")

        if save_to_file:
            try:
                with open(self.output_file_path, 'w', encoding='utf-8') as outfile:
                    outfile.write(final_output)
                logging.info(f"Concatenated text saved to file: {self.output_file_path}")
            except Exception as e:
                self.error_log.append(f"Failed to save file: {str(e)}")
                QMessageBox.critical(self, "Save Error", f"Failed to save file: {str(e)}")
                logging.exception(f"Failed to save file: {str(e)}")
            else:
                QMessageBox.information(
                    self,
                    "Success",
                    f"Files have been concatenated successfully.\nSaved to {self.output_file_path}\n\n"
                    f"Words: {word_count}\nCharacters: {char_count}\nTotal Length: {total_length} characters."
                )
                logging.info("Success message displayed to user.")

        # Show summary in status
        if not error_list:
            self.status_label.setText("Status: Completed Successfully.")
            logging.info("Concatenation completed successfully without errors.")
        else:
            self.status_label.setText("Status: Completed with Errors.")
            logging.info("Concatenation completed with errors.")

        # Re-enable UI elements
        self.toggle_ui(True)
        self.cancel_button.setEnabled(False)

    def generate_file_tree(self, files):
        # Build a nested dictionary to represent the tree
        tree = {}
        for file_path in files:
            try:
                parts = os.path.relpath(file_path, self.selected_directory).split(os.sep)
                current_level = tree
                for part in parts[:-1]:
                    current_level = current_level.setdefault(part, {})
                current_level[parts[-1]] = None  # File
            except ValueError:
                # In case file_path is not under selected_directory
                tree[os.path.basename(file_path)] = None

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
        # Selection Tab
        self.select_dir_button.setEnabled(enabled)
        self.tree_widget.setEnabled(enabled)
        self.select_all_button.setEnabled(enabled)
        self.deselect_all_button.setEnabled(enabled)
        self.search_bar.setEnabled(enabled)

        # Preferences Tab
        self.all_files_radio.setEnabled(enabled)
        self.git_tracked_radio.setEnabled(enabled)
        self.ignore_dir_list.setEnabled(enabled)
        self.add_ignore_dir_button.setEnabled(enabled)
        self.remove_ignore_dir_button.setEnabled(enabled)
        self.ignore_file_list.setEnabled(enabled)
        self.add_ignore_file_button.setEnabled(enabled)
        self.remove_ignore_file_button.setEnabled(enabled)
        self.available_filetypes.setEnabled(enabled)
        self.selected_filetypes.setEnabled(enabled)
        self.add_filetype_button.setEnabled(enabled)
        self.remove_filetype_button.setEnabled(enabled)
        self.custom_filetype_input.setEnabled(enabled)
        self.add_custom_filetype_button.setEnabled(enabled)

        # Output Tab
        self.save_to_file_radio.setEnabled(enabled)
        self.copy_to_clipboard_radio.setEnabled(enabled)
        self.simultaneous_checkbox.setEnabled(enabled)
        self.select_save_button.setEnabled(enabled and (self.save_to_file_radio.isChecked() or self.simultaneous_checkbox.isChecked()))
        self.save_path_edit.setEnabled(enabled and (self.save_to_file_radio.isChecked() or self.simultaneous_checkbox.isChecked()))
        self.preview_text.setEnabled(enabled)

        # Buttons
        self.generate_button.setEnabled(enabled)
        if not enabled:
            self.generate_button.setText("Generating...")
        else:
            self.generate_button.setText("Generate (Ctrl+G)")
        logging.debug(f"UI toggled to {'enabled' if enabled else 'disabled'}.")

    def closeEvent(self, event):
        self.save_config()
        try:
            if hasattr(self, 'thread') and self.thread.isRunning():
                self.thread.terminate()
                logging.info("Application closed while concatenation thread was running.")
        except:
            pass
        event.accept()
        logging.info("Application closed.")

    def update_text_file_extensions(self):
        # Initialize text_file_extensions based on selected_filetypes
        self.text_file_extensions = set()
        for i in range(self.selected_filetypes.count()):
            item = self.selected_filetypes.item(i)
            if item.checkState() == Qt.Checked:
                self.text_file_extensions.add(item.text().lower())
        logging.debug(f"Updated text file extensions: {self.text_file_extensions}")

def main():
    app = QApplication(sys.argv)
    window = ConcatenatorApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

