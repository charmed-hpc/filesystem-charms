# Filesystem client operator

A [Juju](https://juju.is) operator for mounting filesystems.

[![Charmhub Badge](https://charmhub.io/filesystem-client/badge.svg)](https://charmhub.io/filesystem-client)
[![CI](https://github.com/charmed-hpc/filesystem-client-operator/actions/workflows/ci.yaml/badge.svg)](https://github.com/charmed-hpc/filesystem-client-operator/actions/workflows/ci.yaml/badge.svg)
[![Publish](https://github.com/charmed-hpc/filesystem-client-operator/actions/workflows/publish.yaml/badge.svg)](https://github.com/charmed-hpc/filesystem-client-operator/actions/workflows/publish.yaml/badge.svg)
[![Matrix](https://img.shields.io/matrix/ubuntu-hpc%3Amatrix.org?logo=matrix&label=ubuntu-hpc)](https://matrix.to/#/#ubuntu-hpc:matrix.org)


The filesystem client operator requests and mounts exported filesystems on virtual machines.

## ✨ Getting started

1. Deploy a filesystem provider (microceph with ceph-fs in this case), filesystem-client, and a machine to mount the filesystem on:

```shell
juju add-model store
juju deploy -n 3 microceph \
  --channel latest/edge \
  --storage osd-standalone='2G,3' \
  --constraints="virt-type=virtual-machine root-disk=10G mem=4G"
juju deploy ceph-fs --channel latest/edge
juju deploy filesystem-client --channel latest/edge \
    --config mountpoint='/scratch' \
    --config noexec=true
juju deploy ubuntu --base ubuntu@24.04 --constraints virt-type=virtual-machine
```

2. Integrate everything, and that's it!

```shell
juju integrate microceph:mds ceph-fs:ceph-mds
juju integrate filesystem-client:filesystem ceph-fs:filesystem
juju integrate ubuntu:juju-info data:juju-info
```

## 🤝 Project and community

The filesystem client operator is a project of the [Ubuntu High-Performance Computing community](https://ubuntu.com/community/governance/teams/hpc).
It is an open source project that is welcome to community involvement, contributions, suggestions, fixes, and
constructive feedback. Interested in being involved with the development of the filesystem client operator? Check out these links below:

* [Join our online chat](https://matrix.to/#/#ubuntu-hpc:matrix.org)
* [Contributing guidelines](./CONTRIBUTING.md)
* [Code of conduct](https://ubuntu.com/community/ethos/code-of-conduct)
* [File a bug report](https://github.com/charmed-hpc/filesystem-client-operator/issues)
* [Juju SDK docs](https://juju.is/docs/sdk)

## 📋 License

The filesystem client operator is free software, distributed under the
Apache Software License, version 2.0. See the [LICENSE](./LICENSE) file for more information.
