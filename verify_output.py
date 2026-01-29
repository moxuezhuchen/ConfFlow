from confflow.core.console import console, print_info, print_step_header
import sys

print("--- Testing Console Output ---")
print_info("This is an info message.")
print_step_header(1, 1, "Test Step", "TEST", 42)
