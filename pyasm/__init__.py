import sys
import re
from ast import literal_eval
from dataclasses import dataclass
from io import StringIO
from types import CodeType

import uncompyle6.main
from xdis.op_imports import op_imports, canonic_python_version

@dataclass
class Instruction:
	line_num: int
	offset: int
	opname: str
	arg: int
	argval: object

def dis_to_instructions(disasm):
	""" converts output of dis.dis into list of instructions"""
	line_num = None
	instructions = []
	for line in disasm.split("\n"):
		match = re.search(
			r"( ?(?P<line_num>\d+)[ >]+)?(?P<offset>\d+) (?P<opname>[A-Z_]+)(?:\s+(?P<arg>\d+)(?: \((?P<argval>.+)\))?)?",
			line
		)
		if match is not None:
			if match["line_num"]:
				line_num = int(match["line_num"])
			offset = int(match["offset"])
			opname = match["opname"]
			if match["arg"] is not None:
				arg = int(match["arg"])
			else:
				arg = None
			argval = match["argval"]
			instructions.append(Instruction(line_num, offset, opname, arg, argval))
	return instructions

def list_to_bytecode(l, opc):
	"""Convert list of (opname, arg) tuples to bytecode
	based on https://github.com/rocky/python-xdis/blob/master/xdis/bytecode.py#L433
    """
	bc = bytearray()
	extended_arg = False
	for i, opcodes in enumerate(l):
		opname = opcodes[0]
		operands = opcodes[1:]
		if opname not in opc.opname:
			raise TypeError("error at item %d [%s, %s], opcode not valid" % (i, opname, operands))
		opcode = opc.opmap[opname]
		bc.append(opcode)
		if extended_arg:
			operands = (operands[0] & 0xff, *operands[1:])
		if opname == "EXTENDED_ARG":
			extended_arg = True
			bc.append(l[i + 1][1] >> 8)
			continue
		else:
			extended_arg = False
		
		if operands:
			bc.extend(operands)
		else:
			bc.append(0)
	return bytes(bc)

def instructions_to_code(
	instructions, code_objects=None, version=None, name="main", filename="out.py", flags=0
):
	""" converts list of instruction into a code object"""
	if code_objects is None:
		code_objects = {}
	
	arg_names = []
	var_dict = {}
	const_dict = {}
	globals_dict = {}
	names_dict = {}
	cellvars_dict = {}
	
	lineno = None
	prev_offset = 0
	lnotab = bytearray()
	
	# https://github.com/rocky/python-xdis/blob/master/xdis/op_imports.py#L182
	if version is None:
		version = ".".join(map(str, sys.version_info[:3]))
	opcodes = op_imports[canonic_python_version[version]]
	
	for instruction in instructions:
		if lineno is None:
			lineno = instruction.line_num
		elif instruction.line_num > lineno:
			#see https://github.com/python/cpython/blob/master/Objects/lnotab_notes.txt
			offset_increment = instruction.offset - prev_offset
			lineno_increment = instruction.line_num - lineno
			while offset_increment >= 256:
				lnotab.append(255)
				lnotab.append(0)
				offset_increment -= 255
			lnotab.append(offset_increment)
			lnotab.append(lineno_increment % 255)
			while lineno_increment >= 256:
				lnotab.append(0)
				lnotab.append(255)
				lineno_increment -= 255
			
			prev_offset = instruction.offset
			lineno = instruction.line_num
		elif instruction.line_num < lineno:
			raise ValueError(f"{instruction} line_num is greater than actual {lineno}")
		
		opname = instruction.opname
		if opname == "LOAD_CONST":
			if instruction.argval.startswith("<code"):
				obj_name = get_code_obj_name(instruction.argval)
				code_obj = code_objects[obj_name]
				const_dict[instruction.arg] = code_obj
			else:
				const_dict[instruction.arg] = literal_eval(instruction.argval)
		elif opname in ("LOAD_GLOBAL", "STORE_GLOBAL"):
			globals_dict[instruction.arg] = instruction.argval
			names_dict[instruction.arg] = instruction.argval
		elif opname == "LOAD_FAST":
			var_name = instruction.argval
			if var_name not in var_dict.values():
				arg_names.append(var_name)
				var_dict[instruction.arg] = var_name
		elif opname in ("LOAD_NAME", "STORE_NAME", "LOAD_METHOD", "LOAD_ATTR"):
			names_dict[instruction.arg] = instruction.argval
		elif opname in ("LOAD_DEREF", "LOAD_CLOSURE", "STORE_DEREF"):
			if (
				instruction.arg in cellvars_dict and
				cellvars_dict[instruction.arg] != instruction.argval
			):
				raise ValueError(instruction, cellvars_dict)
			cellvars_dict[instruction.arg] = instruction.argval
		elif opname == "STORE_FAST":
			var_dict[instruction.arg] = instruction.argval
		elif opname in ("YIELD_ITER", "YIELD_FROM"):
			flags |= 0x20
	
	argcount = len(arg_names)
	posonlyargcount = 0
	kwonlyargcount = 0
	nlocals = len(var_dict)
	stacksize = 100
	codestring = list_to_bytecode(
		[
		(instruction.opname, ) if instruction.arg is None else
		(instruction.opname, instruction.arg) for instruction in instructions
		], opcodes
	)
	if const_dict:
		consts = tuple(const_dict.get(i, None) for i in range(max(const_dict.keys()) + 1))
	else:
		consts = ()
	if names_dict:
		names = tuple(names_dict.get(i, "") for i in range(max(names_dict.keys()) + 1))
	else:
		names = ()
	if var_dict:
		varnames = tuple(var_dict.get(i, "") for i in range(max(var_dict.keys()) + 1))
	else:
		varnames = ()
	firstlineno = instructions[0].line_num
	freevars = ()
	if cellvars_dict:
		cellvars = tuple(cellvars_dict.get(i, "") for i in range(max(cellvars_dict.keys()) + 1))
	else:
		cellvars = ()
	return CodeType(
		argcount, posonlyargcount, kwonlyargcount, nlocals, stacksize, flags, codestring, consts,
		names, varnames, filename, name, firstlineno, bytes(lnotab), freevars, cellvars
	), arg_names

def get_code_obj_name(s):
	match = re.match(r"<code object <?(.*?)>? at (0x[0-9a-f]+).*>", s)
	assert match is not None
	return f"<{match.group(1)}>", match.group(2)

def split_funcs(disasm):
	""" splits out comprehensions from the main func or functions from the module"""
	start_positions = [0]
	end_positions = []
	names = []
	
	if not disasm.startswith("Disassembly"):
		names.append("main")
	
	for match in re.finditer(r"Disassembly of (.+):", disasm):
		end_positions.append(match.start())
		start_positions.append(match.end())
		name = match.group(1)
		if name.startswith("<"):
			names.append(get_code_obj_name(name))
		else:
			names.append(name)
	
	end_positions.append(len(disasm))
	if disasm.startswith("Disassembly"):
		start_positions.pop(0)
		end_positions.pop(0)
	
	return {
		name: disasm[start:end]
		for start, end, name in zip(start_positions, end_positions, names)
	}

def asm(disasm, code_objects=None, version=None, name="main", filename="out.py", flags=0):
	""" assembles dis.dis output into a code object"""
	instructions = dis_to_instructions(disasm)
	return instructions_to_code(
		instructions,
		code_objects=code_objects,
		version=version,
		name=name,
		filename=filename,
		flags=flags
	)

def asm_all(disasm, filename="out.py", version=None):
	""" assembles all functions within a dis.dis output"""
	disasm = re.sub(r"^#.*\n?", "", disasm, re.MULTILINE).strip()  # ignore comments
	code_objects = {}
	
	funcs = split_funcs(disasm)
	
	assembled = {}
	for name, func in reversed(funcs.items()):
		if isinstance(name, tuple):  # list comps and lambdas
			real_name = name[0]
		else:
			real_name = name
		code, arg_names = asm(
			func, code_objects=code_objects, version=version, name=real_name, filename=filename
		)
		code_objects[name] = code
		
		assembled[real_name] = (code, arg_names)
	
	for name in funcs.keys():  # ensure output is in original order
		if isinstance(name, tuple):  # list comps and lambdas
			real_name = name[0]
		else:
			real_name = name
		yield real_name, *assembled[real_name]

def decompile(disasm, filename="out.py", version=None):
	""" decompiles a disassembly """
	if version is None:
		version = ".".join(map(str, sys.version_info[:3]))
	
	for name, code, arg_names in asm_all(disasm, filename, version):
		out = StringIO()
		uncompyle6.main.decompile(
			co=code, bytecode_version=tuple(map(int, version.split("."))), out=out
		)
		decompiled = "\n".join(
			line for line in out.getvalue().split("\n") if not line.startswith("# ")
		)
		yield name, decompiled, arg_names

def pretty_decompile(disasm, filename="out.py", version=None, tab_char="    "):
	""" decompiles disassembly in human-readable form """
	ret = []
	for name, code, arg_names in decompile(disasm, filename, version):
		if not name.startswith("<"):
			ret.append(
				f"def {name}({', '.join(arg_names)}):\n" +
				"\n".join(tab_char + line for line in code.split("\n"))
			)
	return "\n".join(ret)

def main():
	import argparse
	import sys
	parser = argparse.ArgumentParser(description="Decompiles dis.dis output")
	parser.add_argument("file", nargs="?", type=argparse.FileType("r"), default=sys.stdin)
	parser.add_argument("-f", "--filename", default="out.py")
	parser.add_argument("-o", "--output", type=argparse.FileType("r"), default=sys.stdout)
	parser.add_argument("-v", "--version", default=None)
	args = parser.parse_args()
	with args.file as f:
		with args.output as out:
			print(pretty_decompile(f.read(), args.filename, args.version), file=out)

if __name__ == "__main__":
	main()
