# Directory Auto-Sync

A lightweight C++ utility for automatically mirroring a local directory to mounted drives using rsync and inotify. Ideal for maintaining real-time backups of telemetry data or other important files across multiple storage devices.

## Features

- Monitors a source directory for any changes (new files, modifications, deletions)
- Automatically syncs to specified drives when they are mounted
- Supports multiple destination drives
- Uses rsync for efficient and reliable file transfers
- Low system resource usage
- Performs initial sync when program starts

## Prerequisites

- Linux-based operating system
- C++ compiler
- rsync
- Required development packages:
  ```bash
  sudo apt-get install build-essential
  ```

## Building

```bash
g++ -o sync_to_ssds sync_to_ssds.cpp
```

## Usage

```bash
./sync_to_ssds <source_directory> <destination_drive1> [destination_drive2 ...]
```

### Example

```bash
./sync_to_ssds "/home/user/Sample Telemetry Data" "/media/user/USB_DRIVE_E"
```

This will:
1. Perform an initial sync of all files from the source directory to any mounted destination drives
2. Monitor the source directory for any changes
3. Automatically sync changes to all mounted destination drives
4. Continue running until terminated (Ctrl+C)

## How It Works

The program uses two main Linux components:

1. **inotify**: A Linux kernel subsystem that monitors filesystem events
   - Watches for file creation, modification, deletion, and moves
   - Triggers sync operations when changes are detected

2. **rsync**: Handles efficient file synchronization
   - Uses the `-avz` flags for archive mode, verbosity, and compression
   - `--delete` flag ensures destination mirrors source exactly

The program follows this workflow:
1. Verifies drive mount status using `/etc/mtab`
2. Performs initial sync to all mounted drives
3. Sets up inotify watch on source directory
4. Monitors for changes and syncs as needed

## Implementation Details

- Uses `inotify_init()` and `inotify_add_watch()` for file system monitoring
- Monitors these events:
  - `IN_CREATE`: File/directory creation
  - `IN_DELETE`: File/directory deletion
  - `IN_MODIFY`: File modifications
  - `IN_MOVED_FROM`: Source of a move operation
  - `IN_MOVED_TO`: Destination of a move operation
- Batches multiple changes together to prevent excessive sync operations
- Uses system command to execute rsync with optimal flags

## Limitations

- Linux-only due to inotify dependency
- Requires appropriate file system permissions
- Destination drives must be mounted in the filesystem
- No selective file syncing (mirrors entire directory)
- Must be manually terminated to stop monitoring
- Make sure to mount drives manually for WSL using:
  1. sudo mkdir /mnt/<usb_drive_letter>
  2. sudo mount -t drvfs <usb_drive_letter>: /mnt/<usb_drive_Letter> -o uid=$(id -u $USER),gid=$(id -g $USER),metadata


## Error Handling

The program includes basic error handling for:
- Invalid command line arguments
- inotify initialization failures
- Watch creation failures
- Mount point verification
- Sync operation failures
