#include <iostream>
#include <vector>
#include <string>
#include <cstdlib>
#include <sys/inotify.h>
#include <unistd.h>
#include <limits.h>
#include <cstring>
#include <sys/mount.h>
#include <mntent.h>

const int EVENT_SIZE = sizeof(struct inotify_event);
const int BUF_LEN = 1024 * (EVENT_SIZE + 16);

bool is_drive_mounted(const std::string& drive) {
    FILE* mtab = setmntent("/etc/mtab", "r");
    if (mtab == nullptr) {
        std::cerr << "Error opening /etc/mtab" << std::endl;
        return false;
    }

    struct mntent* entry;
    bool mounted = false;
    while ((entry = getmntent(mtab)) != nullptr) {
        std::cout << entry->mnt_dir << std::endl;
        if (std::string(entry->mnt_dir) == drive) {
            mounted = true;
            break;
        }
    }
    printf("%d\n", mounted);

    endmntent(mtab);
    return mounted;
}

void sync_directory(const std::string& src, const std::string& dest) {
    std::string command = "rsync -avz --delete " + src + " " + dest;
    int result = system(command.c_str());
    if (result != 0) {
        std::cerr << "Error syncing " << src << " to " << dest << std::endl;
    } else {
        std::cout << "Successfully synced " << src << " to " << dest << std::endl;
    }
}

void sync_to_all_drives(const std::string& src, const std::vector<std::string>& drives) {
    for (const auto& drive : drives) {
        if (is_drive_mounted(drive)) {
            sync_directory(src, drive);
        }
    }
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        std::cerr << "Usage: " << argv[0] << " <source_directory> <destination_drive1> [destination_drive2] ..." << std::endl;
        return 1;
    }

    std::string src_dir = argv[1];
    std::vector<std::string> dest_drives(argv + 2, argv + argc);

    int fd = inotify_init();
    if (fd < 0) {
        std::cerr << "Error initializing inotify" << std::endl;
        return 1;
    }

    int wd = inotify_add_watch(fd, src_dir.c_str(), IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM | IN_MOVED_TO);
    if (wd < 0) {
        std::cerr << "Error adding watch to " << src_dir << std::endl;
        close(fd);
        return 1;
    }

    std::cout << "Watching " << src_dir << " for changes..." << std::endl;
    
    // Initial sync
    sync_to_all_drives(src_dir, dest_drives);

    char buffer[BUF_LEN];
    while (true) {
        int length = read(fd, buffer, BUF_LEN);
        if (length < 0) {
            std::cerr << "Error reading inotify events" << std::endl;
            break;
        }

        int i = 0;
        while (i < length) {
            struct inotify_event* event = (struct inotify_event*)&buffer[i];
            if (event->len) {
                if (event->mask & (IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM | IN_MOVED_TO)) {
                    std::cout << "Change detected in " << src_dir << std::endl;
                    sync_to_all_drives(src_dir, dest_drives);
                    break;  // Only sync once per batch of events
                }
            }
            i += EVENT_SIZE + event->len;
        }
    }

    inotify_rm_watch(fd, wd);
    close(fd);
    return 0;
}

