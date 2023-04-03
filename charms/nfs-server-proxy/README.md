# Charmed NFS Server Proxy Operator

> Warning: This charm is currently under heavy feature development and 
> is subject to breaking changes. It should not be used for production-level
> deployments until a stable version of this charm is released.

## Description

This charmed operator enables charmed NFS clients to mount exported NFS shares
on non-charm NFS servers. Network File System (NFS) is a distributed file system 
protocol for sharing files between heterogeneous environments over a network.
This proxy enables human operators to manually set an NFS share endpoint that
charmed NFS clients can mount.

## Usage

### Basic Usage

First, launch a virtual machine using [Multipass](https://multipass.run):

```shell
snap install multipass --classic
multipass launch --name nfs-share jammy
multipass shell nfs-share
```

Inside the Multipass VM, set up an NFS server that exports
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
> by using the command `showmount -e localhost` while inside the Multipass VM

Grab the network address of the Multipass VM and then exit:

```shell
hostname -I
exit
```

Now deploy an NFS server proxy operator with an NFS client operator + principle charm:

```shell
juju deploy nfs-server-proxy kernel-server-proxy --config \
  endpoint=<IPv4 address of Multipass VM>:/data
juju deploy nfs-client data --config mountpoint=/data
juju deploy ubuntu --base ubuntu@22.04
juju integrate data:juju-info ubuntu:juju-info
juju integrate data:nfs-share kernel-server-proxy:nfs-share
```

Once the deployment stabilizes, the _data_ application should have the
following status:

```text
NFS share mounted at /data
```

## Integrations

### _`nfs-share`_

Integrate an NFS server and client over the `nfs_share` interface. This integration
is used to forward the configured NFS share endpoint to charmed NFS clients.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on 
enhancements to this charm following best practice guidelines, and 
CONTRIBUTING.md for developer guidance.

## License

The Charmed NFS Server Proxy Operator is free software, distributed under the
Apache Software License, version 2.0. See LICENSE for more information.
