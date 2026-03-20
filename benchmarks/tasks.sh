#!/usr/bin/env bash
# Benchmark task definitions — sourced by bench scripts

TASK1_NAME="amazon_price"
TASK1_PROMPT="Find the price and shipping cost of a Logitech MX Master 3S mouse on amazon.de. Return the product name, price, and shipping info."

TASK2_NAME="spiegel_headlines"
TASK2_PROMPT="Get the top 5 headlines from spiegel.de. Return each headline as a numbered list."

TASK3_NAME="maps_restaurant"
TASK3_PROMPT="Find vegan Chinese restaurants in Berlin with a rating higher than 4 on Google Maps. Return the top 3 with name, rating, and address."

TASK4_NAME="ddg_fansites"
TASK4_PROMPT="Search DuckDuckGo for penguin fan sites. Return the top 3 results with site name, URL, and a short description."

EVAL_PROMPT_TEMPLATE='You are evaluating a benchmark result. The task was: "%s". The agent returned: """%s""". Rate the result 0-10 on: (1) correctness — did it answer the question? (2) completeness — all requested info present? Return ONLY a JSON object: {"score": N, "reason": "one sentence"}'
