"""

# Text File Concatenator Application

## Description

This Python desktop application provides a graphical user interface (GUI) that allows users to select multiple text files and/or folders from a chosen root directory and concatenate their contents into a single output file. The application is built using PyQt5 and offers a user-friendly interface with progress indicators and error handling.

## Features

- **Select Root Directory:** Choose a root directory from your filesystem.
- **Browse and Select Files/Folders:** Navigate through the directory structure and select multiple files and/or folders. Supports multi-selection.
- **Specify Output File:** Choose the destination directory and specify the name for the concatenated output file.
- **Generate Concatenated File:** Click the "Generate" button to start the concatenation process. The application will recursively read selected folders and include all text-based files.
- **Progress Indicators:** Visual feedback through a progress bar and status messages during the concatenation process.
- **Error Handling:** User-friendly error dialogs for issues like missing selections or file write permissions.

## Dependencies

- Python 3.x
- PyQt5
