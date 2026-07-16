# Filesystem charms

[![CI](https://github.com/canonical/filesystem-charms/actions/workflows/ci.yaml/badge.svg)](https://github.com/canonical/filesystem-charms/actions/workflows/ci.yaml/badge.svg)
[![Publish](https://github.com/canonical/filesystem-charms/actions/workflows/publish.yaml/badge.svg)](https://github.com/canonical/filesystem-charms/actions/workflows/publish.yaml/badge.svg)
[![Matrix](https://img.shields.io/matrix/ubuntu-hpc%3Amatrix.org?logo=matrix&label=ubuntu-hpc)](https://matrix.to/#/#ubuntu-hpc:matrix.org)

[Juju](https://juju.is) charms to manage shared filesystems.

The `filesystem-charms` repository is a collection of charmed operators that enables you to provide,
request, and mount shared filesystems. We currently have:

- [`filesystem-client-operator`](./charms/filesystem-client/): requests and mounts exported filesystems on virtual machines.
- [`nfs-server-proxy-operator`](./charms/nfs-server-proxy/): exports NFS shares from NFS servers not managed by Juju.
- [`cephfs-server-proxy-operator`](./charms/cephfs-server-proxy): exports Ceph filesystems from Ceph clusters not managed by Juju.
- [`lustre-server-proxy-operator`](./charms/lustre-server-proxy): exports Lustre filesystems from Lustre servers not managed by Juju.

## ✨ Getting started

### With a minimal NFS kernel server

First, launch a virtual machine using [LXD](https://ubuntu.com/lxd):

```shell
snap install lxd
lxd init --auto
lxc launch ubuntu:26.04 nfs-server --vm
lxc shell nfs-server
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

Grab the network address of the LXD virtual machine and then exit the current shell session:

```shell
hostname -I
exit
```

Now deploy the NFS server proxy operator with the filesystem client operator and the principal charm:

```shell
juju deploy nfs-server-proxy --channel latest/edge \
    --config hostname=<IPv4 address of LXD virtual machine> \
    --config path=/data
juju deploy filesystem-client data --config mountpoint=/data
juju deploy ubuntu --base ubuntu@26.04
juju integrate data:juju-info ubuntu:juju-info
juju integrate data:filesystem nfs-server-proxy:filesystem
```

### With Microceph

First, launch a virtual machine using [LXD](https://ubuntu.com/lxd):

```shell
snap install lxd
lxd init --auto
lxc launch ubuntu:22.04 cephfs-server --vm
lxc shell cephfs-server
```

Inside the LXD virtual machine, set up [Microceph](https://github.com/canonical/microceph) to export a Ceph filesystem:

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
microceph.ceph fs authorize cephfs client.fs-client / rw
```

To mount a Ceph filesystem, gather the required information and exit:

```shell
export HOST=$(hostname -I | tr -d '[:space:]'):6789
export FSID=$(microceph.ceph -s -f json | jq -r '.fsid')
export CLIENT_KEY=$(microceph.ceph auth print-key client.fs-client)
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
juju deploy ubuntu --base ubuntu@26.04 --constraints virt-type=virtual-machine
juju deploy filesystem-client data --channel latest/edge --config mountpoint=/data
juju integrate data:juju-info ubuntu:juju-info
juju integrate data:filesystem cephfs-server-proxy:filesystem
```

### Mounting using charm provided configuration

If you are a charm author and your charm needs a shared filesystem,
you can integrate with `filesystem-client` using the [`mount_info`] charm library. Assuming
your charm is called `my-charm`, and the charm has been set up to support the `mount` integration,
mounting a filesystem can be accomplished by simply doing:

```shell
juju deploy my-charm
juju deploy filesystem-client data --channel latest/edge
juju integrate data:mount my-charm:mount
```

[`mount_info`]: ./charms/filesystem-client/lib/charms/filesystem_client/v0/mount_info.py

## 🤔 What's next?

If you want to learn more about all the things you can do with the filesystem charms,
here are some further resources for you to explore:

* [Charmed HPC documentation](https://canonical-charmed-hpc.readthedocs-hosted.com/latest/)
* [Open an issue](https://github.com/canonical/filesystem-charms/issues/new?title=ISSUE+TITLE&body=*Please+describe+your+issue*)
* [Ask a question](https://discourse.ubuntu.com/c/project/hpc/151)

## 🛠️ Development

The project uses [just](https://github.com/casey/just) and [uv](https://github.com/astral-sh/uv) for
development, which provides some useful commands that will definitely help while hacking on the charms:

```shell
just repo fmt          # Apply formatting standards to code
just repo lint         # Check code against coding style standards
just repo typecheck    # Type checking
just repo unit         # Run unit tests
just repo integration  # Run integration tests
```

If you're interested in contributing, take a look at our [contributing guidelines](./CONTRIBUTING.md).

## 🤝 Project and community

The filesystem charms are a project of the [Ubuntu High-Performance Computing community](https://ubuntu.com/community/governance/teams/hpc).
Interested in contributing bug fixes, patches, documentation, or feedback? Want to join the
Ubuntu HPC community? You've come to the right place 🤩

Here's some links to help you get started with joining the community:

* [Ubuntu Code of Conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* [Contributing guidelines](./CONTRIBUTING.md)
* [Join the conversation on Matrix](https://matrix.to/#/#hpc:ubuntu.com)
* [Get the latest news or ask and answer questions on the Ubuntu Discourse](https://discourse.ubuntu.com/c/project/hpc/151)

## 📋 License

The filesystem charms are free software, distributed under the
Apache Software License, version 2.0. See the [LICENSE](./LICENSE) file for more information.
