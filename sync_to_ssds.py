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
        self.MIN_FREE_SPACE = 1024 * 1024 * 10  # 10MB minimum free space

    def get_available_space(self, drive):
        usage = shutil.disk_usage(drive)
        return usage.free

    def has_enough_space(self, drive, required_space):
        available = self.get_available_space(drive)
        return available >= (required_space + self.MIN_FREE_SPACE)

    def initial_sync(self):
        for drive in self.mirror_drives:
            if self.is_drive_mounted(drive):
                self.mounted_drives.add(drive)
                self.sync_directory(drive)
            else:
                print(f"Drive {drive} is not mounted. Skipping.")

    def is_drive_mounted(self, drive):
        partitions = psutil.disk_partitions(all=True)
        for partition in partitions:
            if os.path.realpath(drive) == os.path.realpath(partition.mountpoint):
                return True
        return False

    def get_sorted_directories(self, path):
        """Return directories sorted by modification time (newest first)"""
        dirs = []
        for item in Path(path).iterdir():
            if item.is_dir():
                dirs.append((item, item.stat().st_mtime))
        return [d[0] for d in sorted(dirs, key=lambda x: x[1], reverse=True)]

    def sync_directory(self, drive):
        destination = Path(drive) / self.target_dir.name
        if not destination.exists():
            try:
                # Instead of copying entire directory, copy files/folders until space runs out
                destination.mkdir(parents=True, exist_ok=True)
                self.copy_with_space_constraint(self.target_dir, destination)
            except Exception as e:
                print(f"Error creating destination directory {destination}: {e}")
        else:
            self.copy_with_space_constraint(self.target_dir, destination)
            self.delete_extra_files(self.target_dir, destination)

    def copy_with_space_constraint(self, src, dest):
        """Copy files and directories respecting space constraints"""
        # First, handle files in the root directory
        for item in src.iterdir():
            if item.is_file():
                self.copy_file_if_space_available(item, dest / item.name, dest.parent)

        # Then handle subdirectories, newest first
        for dir_path in self.get_sorted_directories(src):
            rel_path = dir_path.relative_to(src)
            dest_dir = dest / rel_path
            dir_size = self.calculate_directory_size(dir_path)
            
            if not dest_dir.exists():
                if self.has_enough_space(dest.parent, dir_size):
                    try:
                        print(f"Copying directory {dest_dir}")
                        shutil.copytree(dir_path, dest_dir)
                        print(f"Copied directory {dest_dir}")
                    except Exception as e:
                        print(f"Error copying directory {dest_dir}: {e}")
                else:
                    print(f"Not enough space for directory {dest_dir}. Skipping.")
                    continue
            else:
                # Update existing directory contents
                self.copy_missing_files(dir_path, dest_dir)

    def copy_file_if_space_available(self, src_file, dest_file, drive):
        """Copy a single file if there's enough space available"""
        if not dest_file.exists() or file_checksum(src_file) != file_checksum(dest_file):
            try:
                file_size = src_file.stat().st_size
                if self.has_enough_space(drive, file_size):
                    print(f"Copying file {dest_file}")
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
                    print(f"Copied file {dest_file}")
                else:
                    print(f"Not enough space to copy file {dest_file}. Skipping.")
            except Exception as e:
                print(f"Error copying file {dest_file}: {e}")

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

    def monitor_drives_old(self):
        while True:
            for drive in self.mirror_drives:
                if self.is_drive_mounted(drive) and drive not in self.mounted_drives:
                    print(f"Drive {drive} mounted.")
                    with self.lock:
                        self.mounted_drives.add(drive)
                        self.sync_directory(drive)
                elif not self.is_drive_mounted(drive) and drive in self.mounted_drives:
                    print(f"Drive {drive} unmounted.")
                    with self.lock:
                        self.mounted_drives.remove(drive)
            time.sleep(5)

    def monitor_drives(self):
        observer = pyudev.MonitorObserver(self.monitor, callback=self.device_event)
        observer.start()
        observer.join()

    def device_event(self, action, device):
        drive_path = device.device_node
        if device.device_node in self.mirror_drives:
            if action == 'add':
                print(f"Drive {drive_path} mounted.")
                with self.lock:
                    self.mounted_drives.add(drive_path)
                    self.sync_directory(drive_path)
            elif action == 'remove':
                print(f"Drive {drive_path} unmounted.")
                with self.lock:
                    self.mounted_drives.discard(drive_path)

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

def parse_arguments():
    parser = argparse.ArgumentParser(description="Mirror a directory to multiple drives.")
    parser.add_argument("target_directory", help="Path to the target directory to monitor.")
    parser.add_argument("mirror_drives", nargs='+', help="List of mirror drive mount points.")
    return parser.parse_args()

def main():
    args = parse_arguments()
    sync_manager = SyncManager(args.target_directory, args.mirror_drives)
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
