FILESYSTEM_CLIENT = "filesystem-client"
MOUNT_PROVIDER = "mount-provider"
NFS_SERVER_PROXY = "nfs-server-proxy"
CEPHFS_SERVER_PROXY = "cephfs-server-proxy"
LUSTRE_SERVER_PROXY = "lustre-server-proxy"
MOUNT_REQUIRERS = ["srv", "shared"]
CHARMS = [
    FILESYSTEM_CLIENT,
    NFS_SERVER_PROXY,
    CEPHFS_SERVER_PROXY,
    MOUNT_PROVIDER,
] + MOUNT_REQUIRERS
