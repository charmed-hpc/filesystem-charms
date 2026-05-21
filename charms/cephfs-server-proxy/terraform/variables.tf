# Copyright 2025-2026 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

variable "app_name" {
  description = "Name for the deployed application"
  type        = string
  default     = "cephfs-server-proxy"
}

variable "base" {
  description = "Operating system base for the charm (for example, ubuntu@24.04)"
  type        = string
  default     = null
}

variable "channel" {
  description = "Charm channel to deploy from"
  type        = string
  default     = "latest/edge"
}

variable "config" {
  description = "Map of charm configuration options"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Constraints string for the charm deployment"
  type        = string
  default     = null
}

variable "machines" {
  description = "List of machine resources to deploy the charm on"
  type        = set(string)
  default     = []
}

variable "model_uuid" {
  description = "UUID of the Juju model to deploy the charm into"
  type        = string
  nullable    = false
}

variable "revision" {
  description = "Charm revision to deploy. Null deploys the latest on the given channel"
  type        = number
  nullable    = true
  default     = null
}

variable "units" {
  description = "Number of application units to deploy"
  type        = number
  default     = 1
}
