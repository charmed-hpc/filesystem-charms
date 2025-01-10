# CephFS server proxy operator

A [Juju](https://juju.is) operator for proxying exported Ceph filesystems.

[![Charmhub Badge](https://charmhub.io/cephfs-server-proxy/badge.svg)](https://charmhub.io/cephfs-server-proxy)
[![CI](https://github.com/canonical/cephfs-server-proxy-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/canonical/cephfs-server-proxy-operator/actions/workflows/ci.yaml/badge.svg)
[![Publish](https://github.com/canonical/cephfs-server-proxy-operator/actions/workflows/publish.yaml/badge.svg)](https://github.com/canonical/cephfs-server-proxy-operator/actions/workflows/publish.yaml/badge.svg)
[![Matrix](https://img.shields.io/matrix/ubuntu-hpc%3Amatrix.org?logo=matrix&label=ubuntu-hpc)](https://matrix.to/#/#ubuntu-hpc:matrix.org)


The CephFS server proxy operator enable CephFS client operators to mount exported Ceph filesystems
on Ceph clusters not managed by Juju. This proxy operator enables Juju users to manually set 
a CephFS endpoint that their CephFS client operators need to mount.

## ✨ Getting started 

#### With Microceph

First, launch a virtual machine using [LXD](https://ubuntu.com/lxd):

```shell
$ snap install lxd
$ lxd init --auto
$ lxc launch ubuntu:24.04 cephfs-server --vm
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

Now deploy the CephFS server proxy operator with a CephFS client operator and principal charm:

```shell
juju add-model ceph
juju deploy cephfs-server-proxy --channel latest/edge \
  --config fsid=<FSID> \
  --config sharepoint=cephfs:/ \
  --config monitor-hosts=<HOST> \
  --config auth-info=fs-client:<CLIENT_KEY>
juju deploy ubuntu --base ubuntu@24.04 --constraints virt-type=virtual-machine
juju deploy cephfs-client data --channel latest/edge --config mountpoint=/data
juju integrate data:juju-info ubuntu:juju-info
juju integrate data:cephfs-share cephfs-server-proxy:cephfs-share
```

## 🤝 Project and community

The CephFS server proxy operator is a project of the [Ubuntu High-Performance Computing community](https://ubuntu.com/community/governance/teams/hpc).
It is an open source project that is welcome to community involvement, contributions, suggestions, fixes, and
constructive feedback. Interested in being involved with the development of the CephFS server proxy operator? Check out these links below:

* [Join our online chat](https://matrix.to/#/#ubuntu-hpc:matrix.org)
* [Contributing guidelines](./CONTRIBUTING.md)
* [Code of conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* [File a bug report](https://github.com/canonical/cephfs-server-proxy-operator/issues)
* [Juju SDK docs](https://juju.is/docs/sdk)

## 📋 License

The CephFS server proxy operator is free software, distributed under the
Apache Software License, version 2.0. See the [LICENSE](./LICENSE) file for more information.
