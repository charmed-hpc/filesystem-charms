active_fss = set(["a", "b", "e"])
mounts = {
    "a": "b",
    "c": "d",
    "e": "f",
    "g": "h"
}

for fs_type in list(mounts.keys()):
    if fs_type not in active_fss:
        # self._mount_manager.umount(mount[fs_type]["mountpoint"])
        del mounts[fs_type]

print(mounts)