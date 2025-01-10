<div align="center">

# NFS server proxy operator

A [Juju](https://juju.is) operator for proxying exported NFS shares.

[![Charmhub Badge](https://charmhub.io/nfs-server-proxy/badge.svg)](https://charmhub.io/nfs-server-proxy)
[![CI](https://github.com/canonical/nfs-server-proxy-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/canonical/nfs-server-proxy-operator/actions/workflows/ci.yaml/badge.svg)
[![Release](https://github.com/canonical/nfs-server-proxy-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/nfs-client-operator/actions/workflows/release.yaml/badge.svg)
[![Matrix](https://img.shields.io/matrix/ubuntu-hpc%3Amatrix.org?logo=matrix&label=ubuntu-hpc)](https://matrix.to/#/#ubuntu-hpc:matrix.org)

</div>

## Features

The NFS server proxy operator enable NFS client operators to mount exported NFS shares
on NFS servers not managed by Juju. This proxy operator enables Juju users to manually set 
an NFS share endpoint that their NFS client operators need to mount.

## Usage

#### With a minimal NFS kernel server

First, launch a virtual machine using [LXD](https://ubuntu.com/lxd):

```shell
$ snap install lxd
$ lxd init --auto
$ lxc launch ubuntu:24.04 nfs-server --vm
$ lxc shell nfs-server
```

Inside the LXD virtual machine, set up an NFS kernel server that exports
a _/data_ directory:

```shell
apt update && apt upgrade
apt install nfs-kernel-server
mkdir -p /data
cat << 'EOF' > /etc/exports
/srv     *(ro,sync,subtree_check)
/data    *(rw,sync,no_subtree_check,no_root_squash)
EOF
exportfs -a
systemctl restart nfs-kernel-server
```

> You can verify if the NFS server is exporting the desired directories
> by using the command `showmount -e localhost` while inside LXD virtual machine.

Grab the network address of the LXD virtual machine and then exit the current shell session:

```shell
hostname -I
exit
```

Now deploy the NFS server proxy operator with an NFS client operator and principal charm:

```shell
$ juju deploy nfs-server-proxy kernel-server-proxy --config \
    endpoint=<IPv4 address of LXD virtual machine>:/data
$ juju deploy nfs-client data --config mountpoint=/data
$ juju deploy ubuntu --base ubuntu@24.04
$ juju integrate data:juju-info ubuntu:juju-info
$ juju integrate data:nfs-share kernel-server-proxy:nfs-share
```

## Project & Community

The NFS server proxy operator is a project of the [Ubuntu HPC](https://discourse.ubuntu.com/t/high-performance-computing-team/35988) 
community. It is an open source project that is welcome to community involvement, contributions, suggestions, fixes, and 
constructive feedback. Interested in being involved with the development of the NFS server proxy operator? Check out these links below:

* [Join our online chat](https://matrix.to/#/#ubuntu-hpc:matrix.org)
* [Contributing guidelines](./CONTRIBUTING.md)
* [Code of conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* [File a bug report](https://github.com/canonical/nfs-server-proxy-operator/issues)
* [Juju SDK docs](https://juju.is/docs/sdk)

## License

The NFS server proxy operator is free software, distributed under the
Apache Software License, version 2.0. See the [LICENSE](./LICENSE) file for more information.
