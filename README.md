# pyasm

`pyasm` decompiles dis.dis output by converting it into a code object and then running `uncompyle6` on the code object. This is intended for use on CTF problems where only this output is provided, if you have a `.pyc` file just use [uncompyle6](https://github.com/rocky/python-uncompyle6/) or one of the various other .pyc decompilers.

*Note: pyasm has only been tested on Python 3.8 disassemblies, using pyasm on disassemblies from other versions may or may not work.*

## Installation

```bash
pip install git+https://github.com/SuperStormer/pyasm.git
```

## Example Usage

```python
$ pyasm samples/disas.txt
def main():
        s = input('Guess?')
        o = b'\xae\xc0\xa1\xab\xef\x15\xd8\xca\x18\xc6\xab\x17\x93\xa8\x11\xd7\x18\x15\xd7\x17\xbd\x9a\xc0\xe9\x93\x11\xa7\x04\xa1\x1c\x1c\xed'
        if e(s) == o:
            print('Correct!')
        else:
            print('Wrong...')
def e(s):
        s = [ord(c) for c in s]
        o = [ord(c) for c in b(a(s), c(s))]
        return bytes(o)
def c(s):
        return [c + 5 for c in s]
def b(s,t):
        for x, y in zip(s, t):
            yield x + y - 50
def a(s):
        o = [
         0] * len(s)
        for i, c in enumerate(s):
            o[i] = c * 2 - 60

        return o
```
