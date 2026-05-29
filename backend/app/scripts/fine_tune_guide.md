# FutureEdge Custom Model Fine-Tuning & Deployment Guide

This guide walks you through taking the `finetuning_dataset.jsonl` bootstrapped from your historical backtests, fine-tuning the **Llama-3-8B-Instruct** model (using Unsloth for ultra-fast, low-memory training), and deploying it locally using **Ollama** or **vLLM** for zero-API-cost trading rationales.

---

## 1. Preparing the Training Dataset
First, run the bootstrapper script to generate your training dataset. This processes your backtester candle logic and compiles expert rationales (win-biased) into standard chat-template inputs.

Run inside the backend docker container:
```bash
docker compose exec backend python -m app.scripts.bootstrap_dataset --period 6mo
```
This generates the file at: `/app/backend/data/finetuning_dataset.jsonl`

---

## 2. Fine-Tuning on Google Colab (using Unsloth)
Unsloth makes fine-tuning 2-5x faster and requires 70% less VRAM, meaning you can train a `Llama-3-8B` model entirely on a free Google Colab T4 GPU.

Create a new notebook in Google Colab, upload your `finetuning_dataset.jsonl` file, and run the following script:

```python
# 1. Install Unsloth & Dependencies
!pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
!pip install --no-deps "trl<0.9.0" peft accelerate bitsandbytes

# 2. Initialize Model & FastLanguageModel
from unsloth import FastLanguageModel
import torch

max_seq_length = 2048 # Supports rope scaling automatically
dtype = None # None for auto detection (Float16/Bfloat16)
load_in_4bit = True # 4bit quantization for low memory

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/llama-3-8b-Instruct-bnb-4bit",
    max_seq_length = max_seq_length,
    dtype = dtype,
    load_in_4bit = load_in_4bit,
)

# 3. Setup LoRA adapters
model = FastLanguageModel.get_peft_model(
    model,
    r = 16, # LoRA Rank (16-64 suggested)
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha = 16,
    lora_dropout = 0, # Optimized to 0
    bias = "none",    # Optimized to "none"
    use_gradient_checkpointing = "unsloth", # Saves memory
    random_state = 3407,
    use_rslora = False,
    loftq_config = None,
)

# 4. Load the dataset
import json
from datasets import Dataset

def load_jsonl_dataset(filepath):
    data = []
    with open(filepath, 'r') as f:
        for line in f:
            data.append(json.loads(line))
    return Dataset.from_list(data)

dataset = load_jsonl_dataset("finetuning_dataset.jsonl")

# Format the dataset using the tokenizer's chat template
def format_prompts(batch):
    texts = []
    for messages in batch["messages"]:
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        texts.append(text)
    return {"text": texts}

dataset = dataset.map(format_prompts, batched=True)

# 5. Define SFT Trainer
from trl import SFTTrainer
from transformers import TrainingArguments

trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    dataset_text_field = "text",
    max_seq_length = max_seq_length,
    dataset_num_proc = 2,
    packing = False, # Can speed up training for short sequences
    args = TrainingArguments(
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 4,
        warmup_steps = 5,
        max_steps = 60, # Adjust based on dataset size (approx. 2-3 epochs)
        learning_rate = 2e-4,
        fp16 = not torch.cuda.is_bf16_supported(),
        bf16 = torch.cuda.is_bf16_supported(),
        logging_steps = 1,
        optim = "adamw_8bit",
        weight_decay = 0.01,
        lr_scheduler_type = "linear",
        seed = 3407,
        output_dir = "outputs",
    ),
)

# 6. Run Training!
trainer_stats = trainer.train()

# 7. Save Model & Export
# Save LoRA Adapters (lightweight 100MB file)
model.save_pretrained("futureedge_lora")

# OR Merge & Save 16-bit (recommended for vLLM deployment)
# model.save_pretrained_merged("futureedge_model_16bit", tokenizer, save_method = "merged_16bit")

# OR Export to GGUF format (recommended for Ollama deployment)
model.save_pretrained_gguf("futureedge_gguf", tokenizer, quantization_method = "q4_k_m")

# 8. Test Inference (Verify model is working directly in Colab)
FastLanguageModel.for_inference(model) # Enable fast inference
test_messages = [
    {
        "role": "system",
        "content": "You are a professional financial AI reasoning engine. Your job is to clearly explain trading decisions to human risk managers."
    },
    {
        "role": "user",
        "content": "You are the reasoning module of an algorithmic trading system for Indian equities.\n\nThe system has just decided to go LONG on RELIANCE.\n\nAgent votes:\n  SignalAgent: BUY (confidence=0.85) — RSI oversold boundaries (28.4)\n  SentimentAgent: BUY (confidence=0.85) — Positive news flows related to RELIANCE earnings beats\n  RiskAgent: HOLD (confidence=0.80) — All risk checks passed\n  PortfolioAgent: HOLD (confidence=0.60) — Portfolio healthy — 0 positions\n\nConsensus scores: buy=1.00, sell=0.00\nRisk score: 0.20\nAgent disagreement: 0.15\nMarket regime: RANGEBOUND\n\nWrite a 2-3 sentence explanation for the risk manager who will review this trade. Explain WHY the system chose LONG, what the key signals were, and what risk factors they should consider. Be specific about the indicators. Be concise. Do not use bullet points. Do not recommend approving or rejecting — just explain the reasoning."
    }
]
inputs = tokenizer.apply_chat_template(test_messages, tokenize=True, add_generation_prompt=True, return_tensors="pt").to("cuda")
outputs = model.generate(input_ids=inputs, max_new_tokens=150, use_cache=True, temperature=0.3)
print("TEST GENERATION RATIONALE:")
print("-" * 50)
print(tokenizer.decode(outputs[0][len(inputs[0]):], skip_special_tokens=True))
print("-" * 50)
```

After running the code in Colab:
- If using **Ollama**, download the `model-unsloth.gguf` file generated inside the `futureedge_gguf` folder.
- If using **vLLM**, download the merged directory `futureedge_model_16bit`.

---

## 3. Local Deployment Options

### Option A: Hosting via Ollama (Easiest)
Ollama runs efficiently on standard Apple Silicon Macs or Windows PCs with GPUs.

1. Download Ollama from [ollama.com](https://ollama.com) and install it.
2. Put the `model-unsloth.gguf` file in a local directory.
3. Create a file named `Modelfile` in the same directory:
   ```dockerfile
   FROM ./model-unsloth.gguf
   TEMPLATE """{{ if .System }}<|start_header_id|>system<|end_header_id|>

   {{ .System }}<|eot_id|>{{ end }}{{ if .Prompt }}<|start_header_id|>user<|end_header_id|>

   {{ .Prompt }}<|eot_id|>{{ end }}<|start_header_id|>assistant<|end_header_id|>

   {{ .Response }}<|eot_id|>"""
   PARAMETER stop "<|start_header_id|>"
   PARAMETER stop "<|end_header_id|>"
   PARAMETER stop "<|eot_id|>"
   ```
4. Build the model inside Ollama:
   ```bash
   ollama create futureedge-reasoner -f Modelfile
   ```
5. Start the model server:
   ```bash
   ollama run futureedge-reasoner
   ```
   Ollama hosts an OpenAI-compatible API on: `http://localhost:11434/v1`

---

### Option B: Hosting via vLLM (Highest Performance)
vLLM is ideal for dedicated Linux server hosting with NVIDIA GPU cards, supporting parallel inference requests with sub-100ms latency.

1. Install vLLM (runs outside of docker on a GPU-enabled instance):
   ```bash
   pip install vllm
   ```
2. Start the OpenAI-compatible vLLM server:
   ```bash
   python -m vllm.entrypoints.openai.api_server \
       --model ./futureedge_model_16bit \
       --port 8000 \
       --served-model-name futureedge-reasoner
   ```
   vLLM hosts an OpenAI-compatible API on: `http://localhost:8000/v1`

---

## 4. Connecting the Custom Model to FutureEdge

Once your local server is running, configure your `.env` file to redirect reasoning calls from OpenAI to your local instance.

Update your `.env` file in the project root:
```env
# Switch LLM reasoning to local custom model
LOCAL_MODEL_BASE_URL="http://localhost:11434/v1"  # Or http://localhost:8000/v1 for vLLM
LOCAL_MODEL_NAME="futureedge-reasoner"
LOCAL_MODEL_API_KEY="dummy_key"                  # Ollama doesn't require a key
```

Uvicorn will auto-detect the changes. The orchestrator will now query your local custom-tuned model instead of GPT-4, completely eliminating API costs and ensuring 100% private execution.
