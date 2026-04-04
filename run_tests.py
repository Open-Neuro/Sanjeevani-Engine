import pytest
import sys

def run():
    with open('test_out_utf8.txt', 'w', encoding='utf-8') as f:
        # We need to capture pytest output. Best way is via pytest plugin or capturing.
        # pytest.main accepts a list of args.
        pass

if __name__ == "__main__":
    with open('test_out_utf8.txt', 'w', encoding='utf-8') as f:
        sys.stdout = f
        sys.stderr = f
        pytest.main(['tests/test_chat.py', '-v'])
