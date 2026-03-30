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
| Databricks PAT | Personal Access Token for authentication |

---

## 1. Find Your Workspace

```bash
az databricks workspace list \
  --query "[].{name:name, url:workspaceUrl}" -o table
```

Note the workspace URL — you'll need it as: `https://<workspace-url>/serving-endpoints`

---

## 2. Generate a Personal Access Token (PAT)

**Option A — Databricks UI:**

1. Open your workspace URL in a browser
2. Click your username (top-right) → **Settings**
3. Go to **Developer** → **Access tokens** → **Generate new token**
4. Set a comment (e.g. `litellm-test`) and lifetime, then copy the token

**Option B — Azure CLI + REST API:**

```bash
WORKSPACE_URL="https://<your-workspace>.azuredatabricks.net"

# Get an Azure AD token for the Databricks resource
DB_TOKEN=$(az account get-access-token \
  --resource 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d \
  -o tsv --query accessToken)

# Create a PAT valid for 24 hours
curl -s -X POST "${WORKSPACE_URL}/api/2.0/token/create" \
  -H "Authorization: Bearer $DB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"comment": "litellm-test", "lifetime_seconds": 86400}'
```

The response contains `token_value` — use that as your `DATABRICKS_API_KEY`.

---

## 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```dotenv
# Databricks credentials
DATABRICKS_API_KEY=dapi...your-personal-access-token...
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
ssh azureuser@$PUBLIC_IP "sudo /opt/litellm-env/bin/pip install 'litellm[proxy]'"
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
  DATABRICKS_API_KEY=<your-pat> \
  DATABRICKS_API_BASE=https://<your-workspace>.azuredatabricks.net/serving-endpoints \
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

LiteLLM supports three authentication methods for Databricks, in order of preference:

### 1. OAuth M2M (recommended for production)

```python
import os
os.environ["DATABRICKS_CLIENT_ID"] = "your-service-principal-app-id"
os.environ["DATABRICKS_CLIENT_SECRET"] = "your-service-principal-secret"
os.environ["DATABRICKS_API_BASE"] = "https://<workspace>.azuredatabricks.net/serving-endpoints"
```

### 2. Personal Access Token (used in this project)

```python
import os
os.environ["DATABRICKS_API_KEY"] = "dapi..."
os.environ["DATABRICKS_API_BASE"] = "https://<workspace>.azuredatabricks.net/serving-endpoints"
```

### 3. Databricks SDK auto-auth

```bash
pip install databricks-sdk
```

```python
# No env vars needed — uses unified auth from Databricks SDK
from litellm import completion
response = completion(model="databricks/databricks-claude-sonnet-4-6", messages=[...])
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
