# Filesystem charms

[![CI](https://github.com/charmed-hpc/filesystem-charms/actions/workflows/ci.yaml/badge.svg)](https://github.com/charmed-hpc/filesystem-charms/actions/workflows/ci.yaml/badge.svg)
[![Publish](https://github.com/charmed-hpc/filesystem-charms/actions/workflows/publish.yaml/badge.svg)](https://github.com/charmed-hpc/filesystem-charms/actions/workflows/publish.yaml/badge.svg)
[![Matrix](https://img.shields.io/matrix/ubuntu-hpc%3Amatrix.org?logo=matrix&label=ubuntu-hpc)](https://matrix.to/#/#ubuntu-hpc:matrix.org)

[Juju](https://juju.is) charms to manage shared filesystems.

The `filesystem-charms` repository is a collection of charmed operators that enables you to provide,
request, and mount shared filesystems. We currently have:


* [`filesystem-operator`](./charms/filesystem-operator/): requests and mounts exported filesystems on virtual machines.
* [`nfs-server-proxy-operator`](./charms/nfs-server-proxy/): exports NFS shares from NFS servers not managed by Juju.
* [`cephfs-server-proxy-operator`](./charms/cephfs-server-proxy): exports Ceph filesystems from Ceph clusters not managed by Juju.

## ✨ Getting started

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
> by using the command `showmount -e localhost` while inside the LXD virtual machine.

Grab the network address of the LXD virtual machine and then exit the current shell session:

```shell
hostname -I
exit
```

Now deploy the NFS server proxy operator with the filesystem client operator and the principal charm:

```shell
$ juju deploy nfs-server-proxy --channel latest/edge \
    --config hostname=<IPv4 address of LXD virtual machine> \
    --config path=/data
$ juju deploy filesystem-client data --config mountpoint=/data
$ juju deploy ubuntu --base ubuntu@24.04
$ juju integrate data:juju-info ubuntu:juju-info
$ juju integrate data:filesystem nfs-server-proxy:filesystem
```

#### With Microceph

First, launch a virtual machine using [LXD](https://ubuntu.com/lxd):

```shell
$ snap install lxd
$ lxd init --auto
$ lxc launch ubuntu:22.04 cephfs-server --vm
$ lxc shell cephfs-server
```

Inside the LXD virtual machine, set up [Microceph](https://github.com/canonical/microceph) to export a Ceph filesystem.

```shell
ln -s /bin/true /usr/local/bin/udevadm
apt-get -y update
apt-get -y install ceph-common jq
snap install microceph
microceph cluster bootstrap
microceph disk add loop,2G,3
microceph.ceph osd pool create cephfs_data
microceph.ceph osd pool create cephfs_metadata
microceph.ceph fs new cephfs cephfs_metadata cephfs_data
microceph.ceph fs authorize cephfs client.fs-client / rw # Creates a new `fs-client` user.
```

> You can verify if the CephFS server is working correctly by using the command
> `microceph.ceph fs status cephfs` while inside the LXD virtual machine.

To mount a Ceph filesystem, you'll require some information that you can get with a couple of commands:

```shell
export HOST=$(hostname -I | tr -d '[:space:]'):6789
export FSID=$(microceph.ceph -s -f json | jq -r '.fsid')
export CLIENT_KEY=$(microceph.ceph auth print-key client.fs-client)
```

Print the required information for reference and then exit the current shell session:

```shell
echo $HOST
echo $FSID
echo $CLIENT_KEY
exit
```

Now deploy the CephFS server proxy operator with the filesystem client operator and the principal charm:

```shell
juju add-model ceph
juju deploy cephfs-server-proxy --channel latest/edge \
  --config fsid=<FSID> \
  --config sharepoint=cephfs:/ \
  --config monitor-hosts=<HOST> \
  --config auth-info=fs-client:<CLIENT_KEY>
juju deploy ubuntu --base ubuntu@24.04 --constraints virt-type=virtual-machine
juju deploy filesysten-client data --channel latest/edge --config mountpoint=/data
juju integrate data:juju-info ubuntu:juju-info
juju integrate data:filesystem cephfs-server-proxy:filesystem
```

## 🤝 Project and community

The filesystem charms are a project of the [Ubuntu High-Performance Computing community](https://ubuntu.com/community/governance/teams/hpc).
It is an open source project that is welcome to community involvement, contributions, suggestions, fixes, and
constructive feedback. Interested in being involved with the development of the filesystem charms? Check out these links below:

* [Join our online chat](https://matrix.to/#/#ubuntu-hpc:matrix.org)
* [Contributing guidelines](./CONTRIBUTING.md)
* [Code of conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* [File a bug report](https://github.com/charmed-hpc/filesystem-charms/issues)
* [Juju SDK docs](https://juju.is/docs/sdk)

## 📋 License

The filesystem charms are free software, distributed under the
Apache Software License, version 2.0. See the [LICENSE](./LICENSE) file for more information.
