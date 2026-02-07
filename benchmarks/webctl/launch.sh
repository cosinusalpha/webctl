source .venv/bin/activate
gemini --model=gemini-2.5-flash-lite --allowed-tools=activate_skill,run_shell_command -p "Go to https://www.amazon.de/ and find the product page of 'Logitech MX Master 3S'.  Extract the current price and the estimated delivery date." --output-format json | tee bench_out.json

