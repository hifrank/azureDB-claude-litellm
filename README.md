# Azure Databricks Model Serving with LiteLLM

Use [LiteLLM](https://docs.litellm.ai/docs/providers/databricks) to call foundation models hosted on [Azure Databricks Model Serving](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/foundation-model-overview) endpoints — including chat completion and embedding models.

## Architecture

```
┌──────────────┐         ┌──────────────────────┐         ┌─────────────────────────────┐
│  Your Code   │──SDK──▶ │  LiteLLM Proxy       │──REST──▶│  Azure Databricks           │
│  (OpenAI SDK)│         │  (on Azure VM)       │         │  Model Serving Endpoint     │
└──────────────┘         └──────────────────────┘         └─────────────────────────────┘
```

LiteLLM wraps the Databricks model serving REST API behind an OpenAI-compatible interface so you can switch between providers (OpenAI, Azure, Databricks, etc.) by changing a single `model=` string.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Azure subscription | With an Azure Databricks workspace deployed |
| Python | 3.9+ (3.11+ recommended) |
| Azure CLI | [Install](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) — needed to create a PAT or VM |
| Service Principal | Azure AD SP for OAuth M2M auth (or PAT for quick testing) |

---

## 1. Find Your Workspace

```bash
az databricks workspace list \
  --query "[].{name:name, url:workspaceUrl}" -o table
```

Note the workspace URL — you'll need it as: `https://<workspace-url>/serving-endpoints`

---

## 2. Create a Service Principal (OAuth M2M)

OAuth M2M with a Service Principal is the recommended authentication method for production.

### Create the Service Principal

```bash
az ad sp create-for-rbac --name "litellm-databricks-sp" --skip-assignment -o json
```

Note the `appId` (= client ID) and `password` (= client secret) from the output.

### Grant access to the Databricks workspace

```bash
WORKSPACE_URL="https://<your-workspace>.azuredatabricks.net"
SP_APP_ID="<appId from above>"
RESOURCE_GROUP="<your-resource-group>"
WORKSPACE_NAME="<your-workspace-name>"

# 1. Assign Contributor role on the workspace resource
az role assignment create \
  --assignee $SP_APP_ID \
  --role "Contributor" \
  --scope $(az databricks workspace show -n $WORKSPACE_NAME -g $RESOURCE_GROUP --query id -o tsv)

# 2. Register the SP in Databricks via SCIM API
DB_TOKEN=$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  -o tsv --query accessToken)

curl -s -X POST "${WORKSPACE_URL}/api/2.0/preview/scim/v2/ServicePrincipals" \
  -H "Authorization: Bearer $DB_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"schemas\": [\"urn:ietf:params:scim:schemas:core:2.0:ServicePrincipal\"],
    \"applicationId\": \"${SP_APP_ID}\",
    \"displayName\": \"litellm-databricks-sp\",
    \"active\": true
  }"
```

### (Optional) PAT-based auth

For quick testing, you can use a Personal Access Token instead:

1. Open your workspace URL → **Settings** → **Developer** → **Access tokens** → **Generate new token**
2. Set `DATABRICKS_API_KEY=dapi...` in `.env` (LiteLLM checks PAT before OAuth)

---

## 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```dotenv
# Azure AD Service Principal credentials (OAuth M2M via Databricks SDK)
ARM_CLIENT_ID=<your-service-principal-application-id>
ARM_CLIENT_SECRET=<your-service-principal-secret>
ARM_TENANT_ID=<your-azure-ad-tenant-id>
DATABRICKS_HOST=https://<your-workspace>.azuredatabricks.net
DATABRICKS_API_BASE=https://<your-workspace>.azuredatabricks.net/serving-endpoints

# LiteLLM proxy settings (set after deploying the proxy VM)
LITELLM_PROXY_URL=http://<proxy-host>:4000/v1
LITELLM_PROXY_KEY=sk-your-proxy-master-key
```

---

## 4. Deploy LiteLLM Proxy on Azure VM

### Create the VM

```bash
RESOURCE_GROUP="<your-resource-group>"
VM_NAME="litellm-proxy-vm"

az vm create \
  --resource-group $RESOURCE_GROUP \
  --name $VM_NAME \
  --image Ubuntu2404 \
  --size Standard_B2s \
  --admin-username azureuser \
  --generate-ssh-keys \
  --custom-data cloud-init.yaml \
  --public-ip-sku Standard
```

The `cloud-init.yaml` auto-installs Python and LiteLLM. Wait for it to finish:

```bash
PUBLIC_IP=$(az vm show -g $RESOURCE_GROUP -n $VM_NAME -d --query publicIps -o tsv)
ssh azureuser@$PUBLIC_IP "cloud-init status --wait"
```

### Install proxy extras

```bash
ssh azureuser@$PUBLIC_IP "sudo /opt/litellm-env/bin/pip install 'litellm[proxy]' databricks-sdk"
```

### Open port 4000 (restricted to your IP)

```bash
MY_IP=$(curl -s https://checkip.amazonaws.com)
NSG_NAME="${VM_NAME}NSG"

az network nsg rule create \
  --resource-group $RESOURCE_GROUP \
  --nsg-name $NSG_NAME \
  --name AllowLiteLLMProxy \
  --priority 1010 \
  --direction Inbound \
  --access Allow \
  --protocol Tcp \
  --destination-port-ranges 4000 \
  --source-address-prefixes $MY_IP

# Also lock down SSH to your IP
az network nsg rule update \
  --resource-group $RESOURCE_GROUP \
  --nsg-name $NSG_NAME \
  --name default-allow-ssh \
  --source-address-prefixes $MY_IP
```

### Upload config and start the proxy

```bash
scp litellm_config.yaml azureuser@$PUBLIC_IP:~/litellm_config.yaml

ssh azureuser@$PUBLIC_IP "nohup env \
  DATABRICKS_HOST=https://<your-workspace>.azuredatabricks.net \
  DATABRICKS_API_BASE=https://<your-workspace>.azuredatabricks.net/serving-endpoints \
  ARM_CLIENT_ID=<your-service-principal-application-id> \
  ARM_CLIENT_SECRET=<your-service-principal-secret> \
  ARM_TENANT_ID=<your-azure-ad-tenant-id> \
  LITELLM_MASTER_KEY=<your-proxy-master-key> \
  /opt/litellm-env/bin/litellm --config ~/litellm_config.yaml --host 0.0.0.0 --port 4000 \
  > ~/litellm_proxy.log 2>&1 &"
```

### Verify the proxy is running

```bash
ssh azureuser@$PUBLIC_IP "tail -5 ~/litellm_proxy.log"
```

Then update your `.env`:

```dotenv
LITELLM_PROXY_URL=http://<PUBLIC_IP>:4000/v1
LITELLM_PROXY_KEY=<your-proxy-master-key>
```

---

## 5. Test

### Using stdlib (no dependencies)

```bash
python3 test_proxy.py
```

### Using OpenAI SDK

```bash
pip install openai python-dotenv
python test_openai_sdk.py
```

### Using curl

```bash
curl -s http://<proxy-host>:4000/v1/chat/completions \
  -H "Authorization: Bearer <your-proxy-master-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "databricks-claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

### Using LiteLLM SDK directly (on the VM)

```bash
ssh azureuser@$PUBLIC_IP "DATABRICKS_API_KEY=<your-pat> \
  DATABRICKS_API_BASE=https://<workspace>.azuredatabricks.net/serving-endpoints \
  /opt/litellm-env/bin/python ~/main.py"
```

---

## Available Models

LiteLLM supports **all** models on your Databricks workspace. Use the `databricks/<model-name>` prefix:

```python
from litellm import completion
response = completion(model="databricks/<model-name>", messages=[...])
```

### Foundation Models (Chat)

| Model | LiteLLM model string |
|---|---|
| Claude Sonnet 4.6 | `databricks/databricks-claude-sonnet-4-6` |
| Claude Sonnet 4.5 | `databricks/databricks-claude-sonnet-4-5` |
| Claude Haiku 4.5 | `databricks/databricks-claude-haiku-4-5` |
| Claude Opus 4.6 | `databricks/databricks-claude-opus-4-6` |
| Llama 3.3 70B | `databricks/databricks-meta-llama-3-3-70b-instruct` |
| Llama 3.1 8B | `databricks/databricks-meta-llama-3-1-8b-instruct` |
| Llama 4 Maverick | `databricks/databricks-llama-4-maverick` |

### Embedding Models

| Model | LiteLLM model string |
|---|---|
| GTE Large EN | `databricks/databricks-gte-large-en` |
| Qwen3 Embedding 0.6B | `databricks/databricks-qwen3-embedding-0-6b` |

> Full list: [Supported foundation models on Mosaic AI Model Serving](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/foundation-model-overview)

---

## Authentication Methods

LiteLLM supports multiple authentication methods for Databricks:

### 1. Azure AD Service Principal via Databricks SDK (recommended for production)

This project uses the Databricks SDK with Azure AD Service Principal credentials.
When no `api_key` is set in the config, LiteLLM falls back to the Databricks SDK,
which auto-authenticates using Azure Identity.

```bash
pip install databricks-sdk
```

```bash
export ARM_CLIENT_ID="your-service-principal-app-id"
export ARM_CLIENT_SECRET="your-service-principal-secret"
export ARM_TENANT_ID="your-azure-ad-tenant-id"
export DATABRICKS_HOST="https://<workspace>.azuredatabricks.net"
export DATABRICKS_API_BASE="https://<workspace>.azuredatabricks.net/serving-endpoints"
```

> **Note:** LiteLLM also has a built-in M2M path using `DATABRICKS_CLIENT_ID` /
> `DATABRICKS_CLIENT_SECRET`, but that uses the Databricks workspace OIDC endpoint
> (`/oidc/v1/token`) which does **not** accept Azure AD SP credentials. For Azure
> Databricks, you must use the Databricks SDK path with `ARM_*` env vars.

### 2. Personal Access Token

```bash
export DATABRICKS_API_KEY="dapi..."
export DATABRICKS_API_BASE="https://<workspace>.azuredatabricks.net/serving-endpoints"
```

### 3. Databricks SDK auto-auth

```bash
pip install databricks-sdk
# Uses unified auth (az login, env vars, etc.) — see Databricks docs
```

---

## Project Structure

```
azureDB-claude-litellm/
├── .env.example          # Template for environment variables
├── .env                  # Your credentials (git-ignored)
├── .gitignore
├── cloud-init.yaml       # VM bootstrap script (installs litellm)
├── litellm_config.yaml   # LiteLLM proxy model configuration
├── main.py               # Chat completion + embedding examples (LiteLLM SDK)
├── test_proxy.py         # Test script — stdlib only
├── test_openai_sdk.py    # Test script — OpenAI SDK
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

---

## Cleanup

To delete the VM and its resources when you're done:

```bash
RESOURCE_GROUP="<your-resource-group>"
VM_NAME="litellm-proxy-vm"

az vm delete -g $RESOURCE_GROUP -n $VM_NAME --yes
az network public-ip delete -g $RESOURCE_GROUP -n ${VM_NAME}PublicIP
az network nic delete -g $RESOURCE_GROUP -n ${VM_NAME}VMNic
az network nsg delete -g $RESOURCE_GROUP -n ${VM_NAME}NSG
az network vnet delete -g $RESOURCE_GROUP -n ${VM_NAME}VNET
```

---

## References

- [LiteLLM Databricks Provider](https://docs.litellm.ai/docs/providers/databricks)
- [Azure Databricks Foundation Model Serving](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/foundation-model-overview)
- [Databricks Personal Access Tokens](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/pat)
