import unittest
import subprocess
import os
import shutil
import tempfile
import filecmp
import time

class SyncToSSDsTest(unittest.TestCase):
    """Test suite for sync_to_ssds.py"""

    def setUp(self):
        """
        Set up the target and mirror directories before each test.
        """
        # Set the target directory to 'Sample_Telemetry_Data'
        self.target_dir = 'Sample_Telemetry_Data'
        
        # Set the mirror directory to '/mnt/e'
        self.mirror_dir1 = '/mnt/e'
        # self.mirror_dir2 = '/mnt/e2'  # If you have multiple mirror drives
        self.mirror_drives = [self.mirror_dir1]

    def tearDown(self):
        """
        Clean up after each test. Since we're using existing directories,
        we typically do not delete them. Uncomment and modify if cleanup is needed.
        """
        # Example cleanup (if necessary):
        # shutil.rmtree(self.target_dir)
        # shutil.rmtree(self.mirror_dir1)
        # shutil.rmtree(self.mirror_dir2)
        pass

    def run_sync(self):
        """
        Execute the sync_to_ssds.py script with the target directory and mirror drives.
        Runs the script for a limited time and then terminates it to prevent infinite looping.
        """
        cmd = ['python', 'sync_to_ssds.py', self.target_dir] + self.mirror_drives
        try:
            # Start the sync_to_ssds.py script as a subprocess
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Allow the script to run for a specified duration (e.g., 5 seconds)
            time_to_run = 5  # seconds
            time.sleep(time_to_run)
            
            # Terminate the subprocess gracefully
            process.terminate()
            
            try:
                # Wait for the process to terminate, with a timeout
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # If the process does not terminate, kill it forcefully
                process.kill()
                process.wait()
            
            # Optionally, capture and handle output or errors
            # stdout, stderr = process.communicate()
            # if process.returncode != 0:
            #     self.fail(f"sync_to_ssds.py failed with return code {process.returncode}.\nStderr: {stderr.decode()}")
        
        except Exception as e:
            self.fail(f"Failed to run sync_to_ssds.py: {e}")

    def compare_files(self, file1, file2):
        """
        Compare two files based on their content.
        """
        return filecmp.cmp(file1, file2, shallow=False)

    def compare_directories_recursively(self, dir1, dir2):
        """
        Recursively compare two directories, including file contents.
        """
        comparison = filecmp.dircmp(dir1, dir2)

        # Check for files/folders only in dir1
        if comparison.left_only:
            self.fail(f"Directory {dir1} has extra items: {comparison.left_only}")

        # Check for files/folders only in dir2
        if comparison.right_only:
            self.fail(f"Directory {dir2} has extra items: {comparison.right_only}")

        # Check for differing files
        for file in comparison.diff_files:
            file1 = os.path.join(dir1, file)
            file2 = os.path.join(dir2, file)
            if not self.compare_files(file1, file2):
                self.fail(f"File contents differ: {file1} vs {file2}")

        # Recursively compare common subdirectories
        for common_dir in comparison.common_dirs:
            self.compare_directories_recursively(
                os.path.join(dir1, common_dir),
                os.path.join(dir2, common_dir)
            )

    def compare_directories(self):
        """
        Compare the contents of the target directory with each mirror drive recursively.
        """
        for mirror in self.mirror_drives:
            self.compare_directories_recursively(self.target_dir, mirror)

    def test_initial_sync(self):
        """
        Test Case 1:
        Run sync_to_ssds.py and verify that mirror drives match the target directory.
        """
        self.run_sync()
        self.compare_directories()

    def test_add_new_file(self):
        """
        Test Case 2:
        Add a new file to the target directory and verify that it is copied to all mirror drives.
        """
        self.run_sync()
        self.compare_directories()

        # Add a new file to the target directory
        new_file_path = os.path.join(self.target_dir, 'new_file.txt')
        with open(new_file_path, 'w') as f:
            f.write('This is a new file added for testing.')

        # Wait for the sync_to_ssds.py to detect and sync the change
        time.sleep(2)  # Adjust sleep time as necessary based on sync_to_ssds.py's polling interval

        self.compare_directories()

    def test_delete_file(self):
        """
        Test Case 3:
        Delete a file from the target directory and verify that it is removed from all mirror drives.
        """
        self.run_sync()
        self.compare_directories()

        # Add a new file to ensure there is something to delete
        new_file_path = os.path.join(self.target_dir, 'new_file.txt')
        with open(new_file_path, 'w') as f:
            f.write('This file will be deleted for testing purposes.')

        # Wait for the sync_to_ssds.py to sync the new file
        time.sleep(2)

        self.compare_directories()

        # Delete the newly added file from the target directory
        os.remove(new_file_path)

        # Wait for the sync_to_ssds.py to detect and sync the deletion
        time.sleep(2)

        self.compare_directories()

if __name__ == '__main__':
    unittest.main()