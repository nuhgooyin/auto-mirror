import os
import shutil
import time
import threading
import argparse
from pathlib import Path
import psutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pyudev
import hashlib
import sys
import inquirer

class SyncManager:
    def __init__(self, target_dir, mirror_drives):
        self.target_dir = Path(target_dir).resolve()
        if not self.target_dir.exists():
            print(f"Error: Target directory '{self.target_dir}' does not exist.")
            sys.exit(1)
        self.mirror_drives = mirror_drives
        self.mounted_drives = set()
        self.lock = threading.Lock()
        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        self.monitor.filter_by('block')
        self.MAX_STORAGE_LIMIT = 0.95  # 95% usage threshold

    def get_available_space(self, drive):
        usage = shutil.disk_usage(drive)
        return usage.free

    def has_enough_space(self, drive, required_space):
        available = self.get_available_space(drive)
        return available >= required_space

    def initial_sync(self):
        for drive in self.mirror_drives:
            if isinstance(drive, dict):
                drive_path = drive['value']
            else:
                drive_path = drive
            if self.is_drive_mounted(drive_path):
                self.mounted_drives.add(drive_path)
                self.sync_directory(drive_path)
            else:
                print(f"Drive {drive_path} is not mounted. Skipping.")

    def is_drive_mounted(self, drive):
        partitions = psutil.disk_partitions(all=True)
        for partition in partitions:
            if os.path.realpath(drive) == os.path.realpath(partition.mountpoint):
                return True
        return False

# TODO: CHECK MAX SIZE ISSUES
    def sync_directory(self, drive):
        destination = Path(drive) / self.target_dir.name
        if not destination.exists():
            try:
                shutil.copytree(self.target_dir, destination)
                print(f"Copied {self.target_dir} to {destination}")
            except Exception as e:
                print(f"Error copying to {destination}: {e}")
        else:
            self.copy_missing_files(self.target_dir, destination)
            self.delete_extra_files(self.target_dir, destination)

    def copy_missing_files(self, src, dest):
        for root, dirs, files in os.walk(src):
            rel_path = Path(root).relative_to(src)
            dest_dir = dest / rel_path
            if not dest_dir.exists():
                try:
                    dir_size = self.calculate_directory_size(root)
                    print(f"Copying directory {dest_dir}")
                    if self.has_enough_space(dest, dir_size):
                        shutil.copytree(root, dest_dir)
                        print(f"Copied directory {dest_dir}")
                    else:
                        print(f"Not enough space to copy directory {dest_dir}. Skipping.")
                except Exception as e:
                    print(f"Error copying directory {dest_dir}: {e}")
            for file in files:
                src_file = Path(root) / file
                dest_file = dest_dir / file
                if not dest_file.exists() or file_checksum(src_file) != file_checksum(dest_file):
                    try:
                        file_size = src_file.stat().st_size
                        print(f"Copying file {dest_file}")
                        if self.has_enough_space(dest, file_size):
                            shutil.copy2(src_file, dest_file)
                            print(f"Copied file {dest_file}")
                        else:
                            print(f"Not enough space to copy file {dest_file}. Skipping.")
                    except Exception as e:
                        print(f"Error copying file {dest_file}: {e}")

    def delete_extra_files(self, src, dest):
        for root, dirs, files in os.walk(dest, topdown=False):
            rel_path = Path(root).relative_to(dest)
            src_dir = src / rel_path
            # Delete files not present in source
            for file in files:
                dest_file = Path(root) / file
                src_file = src_dir / file
                if not src_file.exists():
                    try:
                        os.remove(dest_file)
                        print(f"Deleted file {dest_file}")
                    except Exception as e:
                        print(f"Error deleting file {dest_file}: {e}")
            # Delete directories not present in source
            for dir in dirs:
                dest_subdir = Path(root) / dir
                src_subdir = src_dir / dir
                if not src_subdir.exists():
                    try:
                        shutil.rmtree(dest_subdir)
                        print(f"Deleted directory {dest_subdir}")
                    except Exception as e:
                        print(f"Error deleting directory {dest_subdir}: {e}")

    def monitor_drives(self):
        observer = pyudev.MonitorObserver(self.monitor, callback=self.device_event)
        observer.start()
        observer.join()

    def device_event(self, device):
        if not device.device_node:
            return
            
        # Find the mount point for this device
        partitions = psutil.disk_partitions(all=True)
        for partition in partitions:
            if partition.device == device.device_node:
                mount_point = partition.mountpoint
                if mount_point in self.mirror_drives:
                    action = device.action
                    if action == 'add' or action == 'change':
                        print(f"Drive {mount_point} mounted.")
                        print(f"Syncing drive {mount_point} ...")
                        with self.lock:
                            self.mounted_drives.add(mount_point)
                            self.sync_directory(mount_point)
                    elif action == 'remove':
                        print(f"Drive {mount_point} unmounted.")
                        with self.lock:
                            self.mounted_drives.discard(mount_point)
                    elif action == 'move':
                        print(f"Drive {mount_point} renamed/moved.")
                    else:
                        print("Error: Unhandled device event.")

    def update_changes(self, src_path):
        with self.lock:
            for drive in list(self.mounted_drives):
                destination = Path(drive) / self.target_dir.name
                try:
                    relative_path = Path(src_path).relative_to(self.target_dir)
                except ValueError:
                    # src_path is not under target_dir
                    continue
                dest_path = destination / relative_path
                src_path_obj = Path(src_path)
                if src_path_obj.exists():
                    if src_path_obj.is_dir():
                        if not dest_path.exists():
                            try:
                                dir_size = self.calculate_directory_size(src_path_obj)
                                print(f"Copying directory {dest_path}")
                                if self.has_enough_space(destination, dir_size):
                                    shutil.copytree(src_path_obj, dest_path)
                                    print(f"Copied directory {dest_path}")
                                else:
                                    print(f"Not enough space to copy directory {dest_path}. Skipping.")
                            except Exception as e:
                                print(f"Error copying directory {dest_path}: {e}")
                        else:
                            self.copy_missing_files(src_path_obj, dest_path)
                    else:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            file_size = src_path_obj.stat().st_size
                            print(f"Copying file {dest_path}")
                            if self.has_enough_space(destination, file_size):
                                shutil.copy2(src_path_obj, dest_path)
                                print(f"Copied file {dest_path}")
                            else:
                                print(f"Not enough space to copy file {dest_path}. Skipping.")
                        except Exception as e:
                            print(f"Error copying file {dest_path}: {e}")
                else:
                    # src_path has been deleted, remove from destination
                    if dest_path.exists():
                        try:
                            if dest_path.is_dir():
                                shutil.rmtree(dest_path)
                                print(f"Deleted directory {dest_path}")
                            else:
                                os.remove(dest_path)
                                print(f"Deleted file {dest_path}")
                        except Exception as e:
                            print(f"Error deleting {dest_path}: {e}")
            # After updating changes, ensure no extra files exist
            for drive in list(self.mounted_drives):
                destination = Path(drive) / self.target_dir.name
                self.delete_extra_files(self.target_dir, destination)

    def handle_move(self, src_path, dest_path):
        relative_path = Path(src_path).relative_to(self.target_dir)
        relative_new_path = Path(dest_path).relative_to(self.target_dir)
        for drive in list(self.mounted_drives):
            destination = Path(drive) / self.target_dir.name
            old_dest_path = destination / relative_path
            new_dest_path = destination / relative_new_path
            if old_dest_path.exists():
                try:
                    new_dest_path.parent.mkdir(parents=True, exist_ok=True)
                    print(f"Copying (rename) {old_dest_path} to {new_dest_path}")
                    if self.has_enough_space(destination, 0):  # Rename doesn't require additional space
                        old_dest_path.rename(new_dest_path)
                        print(f"Renamed {old_dest_path} to {new_dest_path} on drive {drive}")
                    else:
                        print(f"Not enough space to rename {old_dest_path} to {new_dest_path} on drive {drive}. Skipping.")
                except Exception as e:
                    print(f"Error renaming {old_dest_path} to {new_dest_path} on drive {drive}: {e}")
            else:
                # If the old path doesn't exist on the mirror, perform a regular update
                self.update_changes(dest_path)

    def calculate_directory_size(self, path):
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total_size += os.path.getsize(fp)
        return total_size

class DirectoryEventHandler(FileSystemEventHandler):
    def __init__(self, sync_manager):
        super().__init__()
        self.sync_manager = sync_manager

    def on_created(self, event):
        if not event.is_directory:
            print(f"File created: {event.src_path}")
            self.sync_manager.update_changes(event.src_path)
        else:
            print(f"Directory created: {event.src_path}")
            self.sync_manager.update_changes(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            print(f"File modified: {event.src_path}")
            self.sync_manager.update_changes(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            print(f"File moved: from {event.src_path} to {event.dest_path}")
            self.sync_manager.handle_move(event.src_path, event.dest_path)
        else:
            print(f"Directory moved: from {event.src_path} to {event.dest_path}")
            self.sync_manager.handle_move(event.src_path, event.dest_path)

    def on_deleted(self, event):
        print(f"{'Directory' if event.is_directory else 'File'} deleted: {event.src_path}")
        self.sync_manager.update_changes(event.src_path)

def get_available_drives():
    """Get list of mounted drives excluding root partition."""
    drives = []
    partitions = psutil.disk_partitions(all=True)
    for partition in partitions:
        # Skip root partition and non-writable locations
        if partition.mountpoint != '/' and not partition.mountpoint.startswith('/boot'):
            drives.append({
                'name': f"{partition.mountpoint} ({partition.device})",
                'value': partition.mountpoint
            })
    return drives

def get_available_directories(current_path='/home'):
    """Get list of directories in the specified path with navigation options."""
    directories = []
    
    # Add parent directory option if not at root
    if current_path != '/':
        directories.append({
            'name': '../ (Go up one level)',
            'value': str(Path(current_path).parent)
        })
    
    try:
        # Add all directories in current path
        for entry in os.scandir(current_path):
            if entry.is_dir() and not entry.name.startswith('.'):
                # Add trailing slash to indicate it's a directory that can be entered
                directories.append({
                    'name': f'{entry.name}/',
                    'value': entry.path
                })
    except PermissionError:
        pass
    
    # Add option to select current directory
    directories.append({
        'name': f'[SELECT] Current Directory: {current_path}',
        'value': f'SELECT:{current_path}'
    })
    
    return directories

def interactive_selection():
    """Prompt user to select target directory and mirror drives."""
    current_path = '/home'
    path_history = [current_path]  # Add this line to store history
    
    while True:
        questions = [
            inquirer.List(
                'target_directory',
                message='Navigate and select the target directory (select [SELECT] option to choose current directory)',
                choices=get_available_directories(current_path),
                # Add these parameters to enable history navigation
                carousel=True,
                default=path_history[-1]
            ),
        ]
        
        answer = inquirer.prompt(questions)
        if not answer:
            sys.exit(1)
            
        selected = answer['target_directory']
        
        # Check if user selected current directory
        if selected['value'].startswith('SELECT:'):
            target_dir = selected['value'].split(':', 1)[1]
            break
        else:
            current_path = selected['value']
            path_history.append(current_path)  # Add new path to history
    
    # After selecting target directory, prompt for mirror drives
    questions = [
        inquirer.Checkbox(
            'mirror_drives',
            message='Select the mirror drives (space to select, enter to confirm, up/down to navigate)',
            choices=get_available_drives(),
            carousel=True,  # Enable wrapping around the list
            default=[]  # Start with no drives selected
        ),
    ]
    
    answers = inquirer.prompt(questions)
    
    if not answers or not answers['mirror_drives']:
        print("Error: You must select at least one mirror drive.")
        sys.exit(1)

    # clean mirror drive input
    cleaned_ans = []
    for answer in answers['mirror_drives']:
        cleaned_ans.append(answer['value'])

    return target_dir, cleaned_ans

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Mirror a directory to multiple drives.",
        epilog="""
Examples:
  # Interactive mode (recommended):
  python %(prog)s -i
  
  # Terminal mode:
  python %(prog)s --target-directory /path/to/dir --mirror-drives /mnt/drive1 /mnt/drive2
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--interactive", "-i", action="store_true", 
                       help="Use interactive mode to select directories and drives")
    parser.add_argument("--target-directory", 
                       help="Path to the target directory to monitor")
    parser.add_argument("--mirror-drives", nargs='+', 
                       help="List of mirror drive mount points")
    
    # If no arguments provided, print help and exit
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
        
    args = parser.parse_args()
    
    if args.interactive:
        return interactive_selection()
    elif args.target_directory and args.mirror_drives:
        return args.target_directory, args.mirror_drives
    else:
        return interactive_selection()

def main():
    args = parse_arguments()
    sync_manager = SyncManager(args[0], args[1])
    sync_manager.initial_sync()

    drive_thread = threading.Thread(target=sync_manager.monitor_drives, daemon=True)
    drive_thread.start()

    event_handler = DirectoryEventHandler(sync_manager)
    observer = Observer()
    observer.schedule(event_handler, path=str(sync_manager.target_dir), recursive=True)
    observer.start()

    print("Monitoring started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

def file_checksum(path):
    hash_func = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_func.update(chunk)
    return hash_func.hexdigest()

if __name__ == "__main__":
    main()
