# Terraform module for filesystem-client

This is a Terraform module facilitating the deployment of the filesystem-client charm using
the [Juju Terraform provider](https://github.com/juju/terraform-provider-juju).
For more information, refer to the
[documentation](https://registry.terraform.io/providers/juju/juju/latest/docs)
for the Juju Terraform provider.

## Requirements

This module requires a Juju model to be available. Refer to the [usage](#usage)
section for more details.

## API

### Inputs

This module offers the following configurable units:

| Name          | Type        | Description                                              | Default           | Required |
|---------------|-------------|----------------------------------------------------------|-------------------|:--------:|
| `app_name`    | string      | Application name                                         | filesystem-client |          |
| `base`        | string      | Base version to use for deployed machine                 | ubuntu@24.04      |          |
| `channel`     | string      | Channel that charm is deployed from                      | latest/edge       |          |
| `config`      | map(string) | Map of charm configuration options to pass at deployment | {}                |          |
| `constraints` | string      | Constraints for the charm deployment                     | "arch=amd64"      |          |
| `model_name`  | string      | Name of the model to deploy the charm to                 |                   |    Y     |
| `revision`    | number      | Revision number of charm to deploy                       | null              |          |

### Outputs

After applying, the module exports the following outputs:

| Name       | Description                 |
|------------|-----------------------------|
| `app_name` | Application name            |
| `requires` | Map of `requires` endpoints |

## Usage

Users should ensure that Terraform is aware of the Juju model dependency of the
charm module.

To deploy this module with its required dependency, you can run
the following command:

```shell
terraform apply -var="model_name=<MODEL_NAME>" -auto-approve
```
