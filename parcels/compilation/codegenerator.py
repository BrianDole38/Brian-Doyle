import ast
from abc import ABC
from abc import abstractmethod
import collections
import math
import numpy as np
import random
from copy import copy

import cgen as c

from parcels.field import Field
from parcels.field import NestedField
from parcels.field import SummedField
from parcels.field import VectorField
from parcels.grid import Grid
from parcels.particle import JITParticle
from parcels.tools.loggers import logger


class IntrinsicNode(ast.AST):
    def __init__(self, obj, ccode):
        self.obj = obj
        self.ccode = ccode


class FieldSetNode(IntrinsicNode):
    def __getattr__(self, attr):
        if isinstance(getattr(self.obj, attr), Field):
            return FieldNode(getattr(self.obj, attr),
                             ccode="%s->%s" % (self.ccode, attr))
        elif isinstance(getattr(self.obj, attr), NestedField):
            if isinstance(getattr(self.obj, attr)[0], VectorField):
                return NestedVectorFieldNode(getattr(self.obj, attr),
                                             ccode="%s->%s" % (self.ccode, attr))
            else:
                return NestedFieldNode(getattr(self.obj, attr),
                                       ccode="%s->%s" % (self.ccode, attr))
        elif isinstance(getattr(self.obj, attr), SummedField) or isinstance(getattr(self.obj, attr), list):
            if isinstance(getattr(self.obj, attr)[0], VectorField):
                return SummedVectorFieldNode(getattr(self.obj, attr),
                                             ccode="%s->%s" % (self.ccode, attr))
            else:
                return SummedFieldNode(getattr(self.obj, attr),
                                       ccode="%s->%s" % (self.ccode, attr))
        elif isinstance(getattr(self.obj, attr), VectorField):
            return VectorFieldNode(getattr(self.obj, attr),
                                   ccode="%s->%s" % (self.ccode, attr))
        else:
            return ConstNode(getattr(self.obj, attr),
                             ccode="%s" % (attr))


class FieldNode(IntrinsicNode):
    def __getattr__(self, attr):
        if isinstance(getattr(self.obj, attr), Grid):
            return GridNode(getattr(self.obj, attr),
                            ccode="%s->%s" % (self.ccode, attr))
        elif attr == "eval":
            return FieldEvalCallNode(self)
        else:
            raise NotImplementedError('Access to Field attributes are not (yet) implemented in JIT mode')


class FieldEvalCallNode(IntrinsicNode):
    def __init__(self, field):
        self.field = field
        self.obj = field.obj
        self.ccode = ""


class FieldEvalNode(IntrinsicNode):
    def __init__(self, field, args, var, convert=True):
        self.field = field
        self.args = args
        self.var = var  # the variable in which the interpolated field is written
        self.convert = convert  # whether to convert the result (like field.applyConversion)


class VectorFieldNode(IntrinsicNode):
    def __getattr__(self, attr):
        if attr == "eval":
            return VectorFieldEvalCallNode(self)
        else:
            raise NotImplementedError('Access to VectorField attributes are not (yet) implemented in JIT mode')

    def __getitem__(self, attr):
        return VectorFieldEvalNode(self.obj, attr)


class VectorFieldEvalCallNode(IntrinsicNode):
    def __init__(self, field):
        self.field = field
        self.obj = field.obj
        self.ccode = ""


class VectorFieldEvalNode(IntrinsicNode):
    def __init__(self, field, args, var, var2, var3, convert=True):
        self.field = field
        self.args = args
        self.var = var  # the variable in which the interpolated field is written
        self.var2 = var2  # second variable for UV interpolation
        self.var3 = var3  # third variable for UVW interpolation
        self.convert = convert  # whether to convert the result (like field.applyConversion)


class SummedFieldNode(IntrinsicNode):
    def __getitem__(self, attr):
        return SummedFieldEvalNode(self.obj, attr)


class SummedFieldEvalNode(IntrinsicNode):
    def __init__(self, fields, args, var):
        self.fields = fields
        self.args = args
        self.var = var  # the variable in which the interpolated field is written


class SummedVectorFieldNode(IntrinsicNode):
    def __getattr__(self, attr):
        if attr == "eval":
            return SummedVectorFieldEvalCallNode(self)

    def __getitem__(self, attr):
        return SummedVectorFieldEvalNode(self.obj, attr)


class SummedVectorFieldEvalCallNode(IntrinsicNode):
    def __init__(self, field):
        self.field = field
        self.obj = field.obj
        self.ccode = ""


class SummedVectorFieldEvalNode(IntrinsicNode):
    def __init__(self, fields, args, var, var2, var3, convert=True):
        self.fields = fields
        self.args = args
        self.var = var  # the variable in which the interpolated field is written
        self.var2 = var2  # second variable for UV interpolation
        self.var3 = var3  # third variable for UVW interpolation
        self.convert = convert  # whether to convert the result (like field.applyConversion)


class NestedFieldNode(IntrinsicNode):
    def __getitem__(self, attr):
        return NestedFieldEvalNode(self.obj, attr)


class NestedFieldEvalNode(IntrinsicNode):
    def __init__(self, fields, args, var):
        self.fields = fields
        self.args = args
        self.var = var  # the variable in which the interpolated field is written


class NestedVectorFieldNode(IntrinsicNode):
    def __getitem__(self, attr):
        return NestedVectorFieldEvalNode(self.obj, attr)


class NestedVectorFieldEvalNode(IntrinsicNode):
    def __init__(self, fields, args, var, var2, var3):
        self.fields = fields
        self.args = args
        self.var = var  # the variable in which the interpolated field is written
        self.var2 = var2  # second variable for UV interpolation
        self.var3 = var3  # third variable for UVW interpolation


class GridNode(IntrinsicNode):
    def __getattr__(self, attr):
        raise NotImplementedError('Access to Grids is not (yet) implemented in JIT mode')


class ConstNode(IntrinsicNode):
    def __getitem__(self, attr):
        return attr


class MathNode(IntrinsicNode):
    symbol_map = {'pi': 'M_PI', 'e': 'M_E', 'nan': 'NAN'}

    def __getattr__(self, attr):
        if hasattr(math, attr):
            if attr in self.symbol_map:
                attr = self.symbol_map[attr]
            return IntrinsicNode(None, ccode=attr)
        else:
            raise AttributeError("""Unknown math function encountered: %s"""
                                 % attr)


class RandomNode(IntrinsicNode):
    symbol_map = {'random': 'parcels_random',
                  'uniform': 'parcels_uniform',
                  'randint': 'parcels_randint',
                  'normalvariate': 'parcels_normalvariate',
                  'expovariate': 'parcels_expovariate',
                  'vonmisesvariate': 'parcels_vonmisesvariate',
                  'seed': 'parcels_seed'}

    def __getattr__(self, attr):
        if hasattr(random, attr):
            if attr in self.symbol_map:
                attr = self.symbol_map[attr]
            return IntrinsicNode(None, ccode=attr)
        else:
            raise AttributeError("""Unknown random function encountered: %s"""
                                 % attr)


class StatusCodeNode(IntrinsicNode):
    symbol_map = {'Success': 'SUCCESS', 'Evaluate': 'EVALUATE',  # StateCodes
                  'Repeat': 'REPEAT', 'Delete': 'DELETE', 'StopExecution': 'STOP_EXECUTION',  # OperationCodes
                  'Error': 'ERROR', 'ErrorInterpolation': 'ERROR_INTERPOLATION',  # ErrorCodes
                  'ErrorOutOfBounds': 'ERROR_OUT_OF_BOUNDS', 'ErrorThroughSurface': 'ERROR_THROUGH_SURFACE',
                  'ErrorTimeExtrapolation': 'ERROR_TIME_EXTRAPOLATION'}

    def __getattr__(self, attr):
        if attr in self.symbol_map:
            attr = self.symbol_map[attr]
            return IntrinsicNode(None, ccode=attr)
        else:
            raise AttributeError("""Unknown status code encountered: %s"""
                                 % attr)


class PrintNode(IntrinsicNode):
    def __init__(self):
        self.obj = 'print'


class GenericParticleAttributeNode(IntrinsicNode):
    def __init__(self, obj, attr, ccode=""):
        super(GenericParticleAttributeNode, self).__init__(obj, ccode)
        self.attr = attr


class ObjectParticleAttributeNode(GenericParticleAttributeNode):
    def __init__(self, obj, attr):
        ccode = "%s->%s" % (obj.ccode, attr)
        super(ObjectParticleAttributeNode, self).__init__(obj, attr, ccode)


class ArrayParticleAttributeNode(GenericParticleAttributeNode):
    def __init__(self, obj, attr):
        ccode = "%s->%s[pnum]" % (obj.ccode, attr)
        super(ArrayParticleAttributeNode, self).__init__(obj, attr, ccode)


class ParticleNode(IntrinsicNode):
    attr_node_class = None

    def __init__(self, obj):
        ccode = ""
        attr_node_class = None
        if 'Array' in obj.name:
            attr_node_class = ArrayParticleAttributeNode
            ccode = 'particles'
        elif 'Object' in obj.name:
            attr_node_class = ObjectParticleAttributeNode
            ccode = 'particle'
        else:
            raise AttributeError("Particle Base Class neither matches an 'Array' nor an 'Object' type - cgen class interpretation invalid.")
        super(ParticleNode, self).__init__(obj, ccode)
        self.attr_node_class = attr_node_class

    def __getattr__(self, attr):
        if attr in [v.name for v in self.obj.variables]:
            return self.attr_node_class(self, attr)
        elif attr in ['delete']:
            return self.attr_node_class(self, 'state')
        else:
            raise AttributeError("""Particle type %s does not define attribute "%s".
Please add '%s' to %s.users_vars or define an appropriate sub-class."""
                                 % (self.obj, attr, attr, self.obj))


class IntrinsicTransformer(ast.NodeTransformer):
    """AST transformer that catches any mention of intrinsic variable
    names, such as 'particle' or 'fieldset', inserts placeholder objects
    and propagates attribute access."""

    def __init__(self, fieldset=None, ptype=JITParticle):
        self.fieldset = fieldset
        self.ptype = ptype

        # Counter and variable names for temporaries
        self._tmp_counter = 0
        self.tmp_vars = []
        # A stack of additonal staements to be inserted
        self.stmt_stack = []

    def get_tmp(self):
        """Create a new temporary veriable name"""
        tmp = "parcels_tmpvar%d" % self._tmp_counter
        self._tmp_counter += 1
        self.tmp_vars += [tmp]
        return tmp

    def visit_Name(self, node):
        """Inject IntrinsicNode objects into the tree according to keyword"""
        if node.id == 'fieldset' and self.fieldset is not None:
            node = FieldSetNode(self.fieldset, ccode='fset')
        elif node.id == 'particle':
            node = ParticleNode(self.ptype)
        elif node.id in ['StateCode', 'OperationCode', 'ErrorCode', 'Error']:
            node = StatusCodeNode(math, ccode='')
        elif node.id == 'math':
            node = MathNode(math, ccode='')
        elif node.id == 'ParcelsRandom':
            node = RandomNode(math, ccode='')
        elif node.id == 'print':
            node = PrintNode()
        elif (node.id == 'pnum') or ('parcels_tmpvar' in node.id):
            raise NotImplementedError("Custom Kernels cannot contain string %s; please change your kernel" % node.id)
        elif node.id == 'abs':
            raise NotImplementedError("abs() does not work in JIT Kernels. Use math.fabs() instead")
        return node

    def visit_Attribute(self, node):
        node.value = self.visit(node.value)
        if isinstance(node.value, IntrinsicNode):
            if node.attr == 'update_next_dt':
                return 'update_next_dt'
            return getattr(node.value, node.attr)
        else:
            if node.value.id in ['np', 'numpy']:
                raise NotImplementedError("Cannot convert numpy functions in kernels to C-code.\n"
                                          "Either use functions from the math library or run Parcels in Scipy mode.\n"
                                          "For more information, see http://oceanparcels.org/faq.html#kernelwriting")
            elif node.value.id in ['random']:
                raise NotImplementedError("Cannot convert random functions in kernels to C-code.\n"
                                          "Use `import parcels.rng as ParcelsRandom` and then ParcelsRandom.random(), ParcelsRandom.uniform() etc.\n"
                                          "For more information, see http://oceanparcels.org/faq.html#kernelwriting")
            else:
                raise NotImplementedError("Cannot convert '%s' used in kernel to C-code" % node.value.id)

    def visit_Subscript(self, node):
        node.value = self.visit(node.value)
        node.slice = self.visit(node.slice)

        # If we encounter field evaluation we replace it with a
        # temporary variable and put the evaluation call on the stack.
        if isinstance(node.value, SummedFieldNode):
            tmp = [self.get_tmp() for _ in node.value.obj]
            # Insert placeholder node for field eval ...
            self.stmt_stack += [SummedFieldEvalNode(node.value, node.slice, tmp)]
            # .. and return the name of the temporary that will be populated
            return ast.Name(id='+'.join(tmp))
        elif isinstance(node.value, SummedVectorFieldNode):
            tmp = [self.get_tmp() for _ in range(len(node.value.obj))]
            tmp2 = [self.get_tmp() for _ in range(len(node.value.obj))]
            tmp3 = [self.get_tmp() if list.__getitem__(node.value.obj, 0).vector_type == '3D' else None for _ in range(len(node.value.obj))]
            # Insert placeholder node for field eval ...
            self.stmt_stack += [SummedVectorFieldEvalNode(node.value, node.slice, tmp, tmp2, tmp3)]
            # .. and return the name of the temporary that will be populated
            if all(tmp3):
                return ast.Tuple([ast.Name(id='+'.join(tmp)), ast.Name(id='+'.join(tmp2)), ast.Name(id='+'.join(tmp3))], ast.Load())
            else:
                return ast.Tuple([ast.Name(id='+'.join(tmp)), ast.Name(id='+'.join(tmp2))], ast.Load())
        elif isinstance(node.value, FieldNode):
            tmp = self.get_tmp()
            # Insert placeholder node for field eval ...
            self.stmt_stack += [FieldEvalNode(node.value, node.slice, tmp)]
            # .. and return the name of the temporary that will be populated
            return ast.Name(id=tmp)
        elif isinstance(node.value, VectorFieldNode):
            tmp = self.get_tmp()
            tmp2 = self.get_tmp()
            tmp3 = self.get_tmp() if node.value.obj.vector_type == '3D' else None
            # Insert placeholder node for field eval ...
            self.stmt_stack += [VectorFieldEvalNode(node.value, node.slice, tmp, tmp2, tmp3)]
            # .. and return the name of the temporary that will be populated
            if tmp3:
                return ast.Tuple([ast.Name(id=tmp), ast.Name(id=tmp2), ast.Name(id=tmp3)], ast.Load())
            else:
                return ast.Tuple([ast.Name(id=tmp), ast.Name(id=tmp2)], ast.Load())
        elif isinstance(node.value, NestedFieldNode):
            tmp = self.get_tmp()
            self.stmt_stack += [NestedFieldEvalNode(node.value, node.slice, tmp)]
            return ast.Name(id=tmp)
        elif isinstance(node.value, NestedVectorFieldNode):
            tmp = self.get_tmp()
            tmp2 = self.get_tmp()
            tmp3 = self.get_tmp() if list.__getitem__(node.value.obj, 0).vector_type == '3D' else None
            self.stmt_stack += [NestedVectorFieldEvalNode(node.value, node.slice, tmp, tmp2, tmp3)]
            if tmp3:
                return ast.Tuple([ast.Name(id=tmp), ast.Name(id=tmp2), ast.Name(id=tmp3)], ast.Load())
            else:
                return ast.Tuple([ast.Name(id=tmp), ast.Name(id=tmp2)], ast.Load())
        else:
            return node

    def visit_AugAssign(self, node):
        node.target = self.visit(node.target)
        node.op = self.visit(node.op)
        node.value = self.visit(node.value)
        stmts = [node]

        # Inject statements from the stack
        if len(self.stmt_stack) > 0:
            stmts = self.stmt_stack + stmts
            self.stmt_stack = []
        return stmts

    def visit_Assign(self, node):
        node.targets = [self.visit(t) for t in node.targets]
        node.value = self.visit(node.value)
        stmts = [node]

        # Inject statements from the stack
        if len(self.stmt_stack) > 0:
            stmts = self.stmt_stack + stmts
            self.stmt_stack = []
        return stmts

    def visit_Call(self, node):
        node.func = self.visit(node.func)
        node.args = [self.visit(a) for a in node.args]
        node.keywords = {kw.arg: self.visit(kw.value) for kw in node.keywords}

        if isinstance(node.func, GenericParticleAttributeNode) \
           and node.func.attr == 'state':
            node = IntrinsicNode(node, "return DELETE")

        elif isinstance(node.func, FieldEvalCallNode):
            # get a temporary value to assign result to
            tmp = self.get_tmp()
            # whether to convert
            convert = True
            if "applyConversion" in node.keywords:
                k = node.keywords["applyConversion"]
                if isinstance(k, ast.NameConstant):
                    convert = k.value

            # convert args to Index(Tuple(*args))
            args = ast.Index(value=ast.Tuple(node.args, ast.Load()))

            self.stmt_stack += [FieldEvalNode(node.func.field, args, tmp, convert)]
            return ast.Name(id=tmp)

        elif isinstance(node.func, VectorFieldEvalCallNode):
            # get a temporary value to assign result to
            tmp1 = self.get_tmp()
            tmp2 = self.get_tmp()
            tmp3 = self.get_tmp() if node.func.field.obj.vector_type == '3D' else None
            # whether to convert
            convert = True
            if "applyConversion" in node.keywords:
                k = node.keywords["applyConversion"]
                if isinstance(k, ast.NameConstant):
                    convert = k.value

            # convert args to Index(Tuple(*args))
            args = ast.Index(value=ast.Tuple(node.args, ast.Load()))

            self.stmt_stack += [VectorFieldEvalNode(node.func.field, args, tmp1, tmp2, tmp3, convert)]
            if tmp3:
                return ast.Tuple([ast.Name(id=tmp1), ast.Name(id=tmp2), ast.Name(id=tmp3)], ast.Load())
            else:
                return ast.Tuple([ast.Name(id=tmp1), ast.Name(id=tmp2)], ast.Load())

        elif isinstance(node.func, SummedVectorFieldEvalCallNode):
            # get a temporary value to assign result to
            tmp = [self.get_tmp() for _ in range(len(node.func.obj))]
            tmp2 = [self.get_tmp() for _ in range(len(node.func.obj))]
            tmp3 = [self.get_tmp() if list.__getitem__(node.func.obj, 0).vector_type == '3D' else None for _ in range(len(node.func.obj))]
            # whether to convert
            convert = True
            if "applyConversion" in node.keywords:
                k = node.keywords["applyConversion"]
                if isinstance(k, ast.NameConstant):
                    convert = k.value

            # convert args to Index(Tuple(*args))
            args = ast.Index(value=ast.Tuple(node.args, ast.Load()))

            self.stmt_stack += [SummedVectorFieldEvalNode(node.func.field, args, tmp, tmp2, tmp3, convert)]
            if all(tmp3):
                return ast.Tuple([ast.Name(id='+'.join(tmp)), ast.Name(id='+'.join(tmp2)), ast.Name(id='+'.join(tmp3))], ast.Load())
            else:
                return ast.Tuple([ast.Name(id='+'.join(tmp)), ast.Name(id='+'.join(tmp2))], ast.Load())

        return node


class TupleSplitter(ast.NodeTransformer):
    """AST transformer that detects and splits Pythonic tuple
    assignments into multiple statements for conversion to C."""

    def visit_Assign(self, node):
        if isinstance(node.targets[0], ast.Tuple) \
           and isinstance(node.value, ast.Tuple):
            t_elts = node.targets[0].elts
            v_elts = node.value.elts
            if len(t_elts) != len(v_elts):
                raise AttributeError("Tuple lenghts in assignment do not agree")
            node = [ast.Assign() for _ in t_elts]
            for n, t, v in zip(node, t_elts, v_elts):
                n.targets = [t]
                n.value = v
        return node


class AbstractKernelGenerator(ABC, ast.NodeVisitor):
    """Code generator class that translates simple Python kernel
    functions into C functions by populating and accessing the `ccode`
    attriibute on nodes in the Python AST."""

    # Intrinsic variables that appear as function arguments
    kernel_vars = ['particle', 'fieldset', 'time', 'output_time', 'tol']
    array_vars = []

    def __init__(self, fieldset=None, ptype=JITParticle):
        self.fieldset = fieldset
        self.ptype = ptype
        self.field_args = collections.OrderedDict()
        self.vector_field_args = collections.OrderedDict()
        self.const_args = collections.OrderedDict()

    def generate(self, py_ast, funcvars):
        # Replace occurences of intrinsic objects in Python AST
        transformer = IntrinsicTransformer(self.fieldset, self.ptype)
        py_ast = transformer.visit(py_ast)

        # Untangle Pythonic tuple-assignment statements
        py_ast = TupleSplitter().visit(py_ast)

        # Generate C-code for all nodes in the Python AST
        self.visit(py_ast)
        self.ccode = py_ast.ccode

        # Insert variable declarations for non-instrinsics
        # Make sure that repeated variables are not declared more than
        # once. If variables occur in multiple Kernels, give a warning
        used_vars = []
        funcvars_copy = copy(funcvars)  # editing a list while looping over it is dangerous
        for kvar in funcvars:
            if kvar in used_vars:
                if kvar not in ['particle', 'fieldset', 'time']:
                    logger.warning(kvar+" declared in multiple Kernels")
                funcvars_copy.remove(kvar)
            else:
                used_vars.append(kvar)
        funcvars = funcvars_copy
        for kvar in self.kernel_vars + self.array_vars:
            if kvar in funcvars:
                funcvars.remove(kvar)
        self.ccode.body.insert(0, c.Value('StatusCode', 'err'))
        if len(funcvars) > 0:
            self.ccode.body.insert(0, c.Value("type_coord", ", ".join(funcvars)))
        if len(transformer.tmp_vars) > 0:
            self.ccode.body.insert(0, c.Value("float", ", ".join(transformer.tmp_vars)))

        return self.ccode

    @staticmethod
    @abstractmethod
    def _check_FieldSamplingArguments(ccode):
        return None

    @abstractmethod
    def visit_FunctionDef(self, node):
        pass

    def visit_Call(self, node):
        """Generate C code for simple C-style function calls. Please
        note that starred and keyword arguments are currently not
        supported."""
        pointer_args = False
        parcels_customed_Cfunc = False
        if isinstance(node.func, PrintNode):
            # Write our own Print parser because Python3-AST does not seem to have one
            if isinstance(node.args[0], ast.Str):
                node.ccode = str(c.Statement('printf("%s\\n")' % (node.args[0].s)))
            elif isinstance(node.args[0], ast.Name):
                node.ccode = str(c.Statement('printf("%%f\\n", %s)' % (node.args[0].id)))
            elif isinstance(node.args[0], ast.BinOp):
                if hasattr(node.args[0].right, 'ccode'):
                    args = node.args[0].right.ccode
                elif hasattr(node.args[0].right, 'id'):
                    args = node.args[0].right.id
                elif hasattr(node.args[0].right, 'elts'):
                    args = []
                    for a in node.args[0].right.elts:
                        if hasattr(a, 'ccode'):
                            args.append(a.ccode)
                        elif hasattr(a, 'id'):
                            args.append(a.id)
                else:
                    args = []
                s = 'printf("%s\\n"' % node.args[0].left.s
                if isinstance(args, str):
                    s = s + (", %s)" % args)
                else:
                    for arg in args:
                        s = s + (", %s" % arg)
                    s = s + ")"
                node.ccode = str(c.Statement(s))
            else:
                raise RuntimeError("This print statement is not supported in Python3 version of Parcels")
        else:
            for a in node.args:
                self.visit(a)
                if a.ccode == 'parcels_customed_Cfunc_pointer_args':
                    pointer_args = True
                    parcels_customed_Cfunc = True
                elif a.ccode == 'parcels_customed_Cfunc':
                    parcels_customed_Cfunc = True
                elif isinstance(a, FieldNode) or isinstance(a, VectorFieldNode):
                    a.ccode = a.obj.ccode_name
                elif isinstance(a, ParticleNode):
                    continue
                elif pointer_args:
                    a.ccode = "&%s" % a.ccode
            ccode_args = ", ".join([a.ccode for a in node.args[pointer_args:]])
            try:
                if isinstance(node.func, str):
                    node.ccode = node.func + '(' + ccode_args + ')'
                else:
                    self.visit(node.func)
                    rhs = "%s(%s)" % (node.func.ccode, ccode_args)
                    if parcels_customed_Cfunc:
                        node.ccode = str(c.Block([c.Assign("err", rhs),
                                                  c.Statement("CHECKSTATUS(err)")]))
                    else:
                        node.ccode = rhs
            except:
                raise RuntimeError("Error in converting Kernel to C. See https://nbviewer.jupyter.org/github/OceanParcels/parcels/blob/master/parcels/examples/tutorial_parcels_structure.ipynb#3.-Kernel-execution for hints and tips")

    def visit_Name(self, node):
        """Catches any mention of intrinsic variable names, such as
        'particle' or 'fieldset' and inserts our placeholder objects"""
        if node.id == 'True':
            node.id = "1"
        if node.id == 'False':
            node.id = "0"
        node.ccode = node.id

    def visit_NameConstant(self, node):
        if node.value is True:
            node.ccode = "1"
        if node.value is False:
            node.ccode = "0"

    def visit_Expr(self, node):
        self.visit(node.value)
        node.ccode = c.Statement(node.value.ccode)

    def visit_Assign(self, node):
        self.visit(node.targets[0])
        self.visit(node.value)
        if isinstance(node.value, ast.List):
            # Detect in-place initialisation of multi-dimensional arrays
            tmp_node = node.value
            decl = c.Value('float', node.targets[0].id)
            while isinstance(tmp_node, ast.List):
                decl = c.ArrayOf(decl, len(tmp_node.elts))
                if isinstance(tmp_node.elts[0], ast.List):
                    # Check type and dimension are the same
                    if not all(isinstance(e, ast.List) for e in tmp_node.elts):
                        raise TypeError("Non-list element discovered in array declaration")
                    if not all(len(e.elts) == len(tmp_node.elts[0].elts) for e in tmp_node.elts):
                        raise TypeError("Irregular array length not allowed in array declaration")
                tmp_node = tmp_node.elts[0]
            node.ccode = c.Initializer(decl, node.value.ccode)
            self.array_vars += [node.targets[0].id]
        else:
            node.ccode = c.Assign(node.targets[0].ccode, node.value.ccode)

    def visit_AugAssign(self, node):
        self.visit(node.target)
        self.visit(node.op)
        self.visit(node.value)
        node.ccode = c.Statement("%s %s= %s" % (node.target.ccode,
                                                node.op.ccode,
                                                node.value.ccode))

    def visit_If(self, node):
        self.visit(node.test)
        for b in node.body:
            self.visit(b)
        for b in node.orelse:
            self.visit(b)
        # field evals are replaced by a tmp variable is added to the stack.
        # Here it means field evals passes from node.test to node.body. We take it out manually
        fieldInTestCount = node.test.ccode.count('parcels_tmpvar')
        body0 = c.Block([b.ccode for b in node.body[:fieldInTestCount]])
        body = c.Block([b.ccode for b in node.body[fieldInTestCount:]])
        orelse = c.Block([b.ccode for b in node.orelse]) if len(node.orelse) > 0 else None
        ifcode = c.If(node.test.ccode, body, orelse)
        node.ccode = c.Block([body0, ifcode])

    def visit_Compare(self, node):
        self.visit(node.left)
        assert (len(node.ops) == 1)
        self.visit(node.ops[0])
        assert (len(node.comparators) == 1)
        self.visit(node.comparators[0])
        node.ccode = "%s %s %s" % (node.left.ccode, node.ops[0].ccode,
                                   node.comparators[0].ccode)

    def visit_Index(self, node):
        self.visit(node.value)
        node.ccode = node.value.ccode

    def visit_Tuple(self, node):
        for e in node.elts:
            self.visit(e)
        node.ccode = tuple([e.ccode for e in node.elts])

    def visit_List(self, node):
        for e in node.elts:
            self.visit(e)
        node.ccode = "{" + ", ".join([e.ccode for e in node.elts]) + "}"

    def visit_Subscript(self, node):
        self.visit(node.value)
        self.visit(node.slice)
        if isinstance(node.value, FieldNode) or isinstance(node.value, VectorFieldNode):
            node.ccode = node.value.__getitem__(node.slice.ccode).ccode
        elif isinstance(node.value, IntrinsicNode):
            raise NotImplementedError("Subscript not implemented for object type %s"
                                      % type(node.value).__name__)
        else:
            node.ccode = "%s[%s]" % (node.value.ccode, node.slice.ccode)

    def visit_UnaryOp(self, node):
        self.visit(node.op)
        self.visit(node.operand)
        node.ccode = "%s(%s)" % (node.op.ccode, node.operand.ccode)

    def visit_BinOp(self, node):
        self.visit(node.left)
        self.visit(node.op)
        self.visit(node.right)
        if isinstance(node.op, ast.BitXor):
            raise RuntimeError("JIT kernels do not support the '^' operator.\n"
                               "Did you intend to use the exponential/power operator? In that case, please use '**'")
        elif node.op.ccode == 'pow':  # catching '**' pow statements
            node.ccode = "pow(%s, %s)" % (node.left.ccode, node.right.ccode)
        else:
            node.ccode = "(%s %s %s)" % (node.left.ccode, node.op.ccode, node.right.ccode)
        node.s_print = True

    def visit_Add(self, node):
        node.ccode = "+"

    def visit_UAdd(self, node):
        node.ccode = "+"

    def visit_Sub(self, node):
        node.ccode = "-"

    def visit_USub(self, node):
        node.ccode = "-"

    def visit_Mult(self, node):
        node.ccode = "*"

    def visit_Div(self, node):
        node.ccode = "/"

    def visit_Mod(self, node):
        node.ccode = "%"

    def visit_Pow(self, node):
        node.ccode = "pow"

    def visit_BoolOp(self, node):
        self.visit(node.op)
        for v in node.values:
            self.visit(v)
        op_str = " %s " % node.op.ccode
        node.ccode = op_str.join([v.ccode for v in node.values])

    def visit_Eq(self, node):
        node.ccode = "=="

    def visit_NotEq(self, node):
        node.ccode = "!="

    def visit_Lt(self, node):
        node.ccode = "<"

    def visit_LtE(self, node):
        node.ccode = "<="

    def visit_Gt(self, node):
        node.ccode = ">"

    def visit_GtE(self, node):
        node.ccode = ">="

    def visit_And(self, node):
        node.ccode = "&&"

    def visit_Or(self, node):
        node.ccode = "||"

    def visit_Not(self, node):
        node.ccode = "!"

    def visit_While(self, node):
        self.visit(node.test)
        for b in node.body:
            self.visit(b)
        if len(node.orelse) > 0:
            raise RuntimeError("Else clause in while clauses cannot be translated to C")
        body = c.Block([b.ccode for b in node.body])
        node.ccode = c.DoWhile(node.test.ccode, body)

    def visit_For(self, node):
        raise RuntimeError("For loops cannot be translated to C")

    def visit_Break(self, node):
        node.ccode = c.Statement("break")

    def visit_Pass(self, node):
        node.ccode = c.Statement("")

    def visit_FieldNode(self, node):
        """Record intrinsic fields used in kernel"""
        self.field_args[node.obj.ccode_name] = node.obj

    def visit_SummedFieldNode(self, node):
        """Record intrinsic fields used in kernel"""
        for fld in node.obj:
            self.field_args[fld.ccode_name] = fld

    def visit_NestedFieldNode(self, node):
        """Record intrinsic fields used in kernel"""
        for fld in node.obj:
            self.field_args[fld.ccode_name] = fld

    def visit_VectorFieldNode(self, node):
        """Record intrinsic fields used in kernel"""
        self.vector_field_args[node.obj.ccode_name] = node.obj

    def visit_SummedVectorFieldNode(self, node):
        """Record intrinsic fields used in kernel"""
        for fld in node.obj:
            self.vector_field_args[fld.ccode_name] = fld

    def visit_NestedVectorFieldNode(self, node):
        """Record intrinsic fields used in kernel"""
        for fld in node.obj:
            self.vector_field_args[fld.ccode_name] = fld

    def visit_ConstNode(self, node):
        self.const_args[node.ccode] = node.obj

    @abstractmethod
    def visit_FieldEvalNode(self, node):
        pass

    @abstractmethod
    def visit_VectorFieldEvalNode(self, node):
        pass

    @abstractmethod
    def visit_SummedFieldEvalNode(self, node):
        pass

    @abstractmethod
    def visit_SummedVectorFieldEvalNode(self, node):
        pass

    @abstractmethod
    def visit_NestedFieldEvalNode(self, node):
        pass

    @abstractmethod
    def visit_NestedVectorFieldEvalNode(self, node):
        pass

    def visit_Return(self, node):
        self.visit(node.value)
        node.ccode = c.Statement('return %s' % node.value.ccode)

    def visit_Print(self, node):
        for n in node.values:
            self.visit(n)
        if hasattr(node.values[0], 's'):
            node.ccode = c.Statement('printf("%s\\n")' % (n.ccode))
            return
        if hasattr(node.values[0], 's_print'):
            args = node.values[0].right.ccode
            s = ('printf("%s\\n"' % node.values[0].left.ccode)
            if isinstance(args, str):
                s = s + (", %s)" % args)
            else:
                for arg in args:
                    s = s + (", %s" % arg)
                s = s + ")"
            node.ccode = c.Statement(s)
            return
        vars = ', '.join([n.ccode for n in node.values])
        int_vars = ['particle->id', 'particle->xi', 'particle->yi', 'particle->zi']
        stat = ', '.join(["%d" if n.ccode in int_vars else "%f" for n in node.values])
        node.ccode = c.Statement('printf("%s\\n", %s)' % (stat, vars))

    def visit_Constant(self, node):
        if node.s == 'parcels_customed_Cfunc_pointer_args':
            node.ccode = node.s
        elif isinstance(node.s, str):
            node.ccode = ''  # skip strings from docstrings or comments
        elif isinstance(node.s, bool):
            node.ccode = "1" if node.s is True else "0"
        else:
            node.ccode = str(node.n)


class ArrayKernelGenerator(AbstractKernelGenerator):

    def __init__(self, fieldset=None, ptype=JITParticle):
        super(ArrayKernelGenerator, self).__init__(fieldset, ptype)

    @staticmethod
    def _check_FieldSamplingArguments(ccode):
        if ccode == 'particles':
            args = ('time', 'particles->depth[pnum]', 'particles->lat[pnum]', 'particles->lon[pnum]')
        elif ccode[-1] == 'particles':
            args = ccode[:-1]
        else:
            args = ccode
        return args

    def visit_FunctionDef(self, node):
        # Generate "ccode" attribute by traversing the Python AST
        for stmt in node.body:
            if not (hasattr(stmt, 'value') and type(stmt.value) is ast.Str):  # ignore docstrings
                self.visit(stmt)

        # Create function declaration and argument list
        decl = c.Static(c.DeclSpecifier(c.Value("StatusCode", node.name), spec='inline'))
        args = [c.Pointer(c.Value(self.ptype.name + 'p', "particles")),
                c.Value("int", "pnum"),
                c.Value("double", "time")]
        for field in self.field_args.values():
            args += [c.Pointer(c.Value("CField", "%s" % field.ccode_name))]
        for field in self.vector_field_args.values():
            for fcomponent in ['U', 'V', 'W']:
                try:
                    f = getattr(field, fcomponent)
                    if f.ccode_name not in self.field_args:
                        args += [c.Pointer(c.Value("CField", "%s" % f.ccode_name))]
                        self.field_args[f.ccode_name] = f
                except:
                    pass  # field.W does not always exist
        for const, _ in self.const_args.items():
            args += [c.Value("float", const)]

        # Create function body as C-code object
        body = [stmt.ccode for stmt in node.body if not (hasattr(stmt, 'value') and type(stmt.value) is ast.Str)]
        body += [c.Statement("return SUCCESS")]
        node.ccode = c.FunctionBody(c.FunctionDeclaration(decl, args), c.Block(body))

    def visit_FieldEvalNode(self, node):
        self.visit(node.field)
        self.visit(node.args)
        args = self._check_FieldSamplingArguments(node.args.ccode)
        ccode_eval = node.field.obj.ccode_eval_array(node.var, *args)
        stmts = [c.Assign("err", ccode_eval)]

        if node.convert:
            ccode_conv = node.field.obj.ccode_convert(*args)
            conv_stat = c.Statement("%s *= %s" % (node.var, ccode_conv))
            stmts += [conv_stat]

        node.ccode = c.Block(stmts + [c.Statement("CHECKSTATUS(err)")])

    def visit_VectorFieldEvalNode(self, node):
        self.visit(node.field)
        self.visit(node.args)
        args = self._check_FieldSamplingArguments(node.args.ccode)
        ccode_eval = node.field.obj.ccode_eval_array(node.var, node.var2, node.var3,
                                                     node.field.obj.U, node.field.obj.V, node.field.obj.W, *args)
        if node.convert and node.field.obj.U.interp_method != 'cgrid_velocity':
            ccode_conv1 = node.field.obj.U.ccode_convert(*args)
            ccode_conv2 = node.field.obj.V.ccode_convert(*args)
            statements = [c.Statement("%s *= %s" % (node.var, ccode_conv1)),
                          c.Statement("%s *= %s" % (node.var2, ccode_conv2))]
        else:
            statements = []
        if node.convert and node.field.obj.vector_type == '3D':
            ccode_conv3 = node.field.obj.W.ccode_convert(*args)
            statements.append(c.Statement("%s *= %s" % (node.var3, ccode_conv3)))
        conv_stat = c.Block(statements)
        node.ccode = c.Block([c.Assign("err", ccode_eval),
                              conv_stat, c.Statement("CHECKSTATUS(err)")])

    def visit_SummedFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld, var in zip(node.fields.obj, node.var):
            ccode_eval = fld.ccode_eval_array(var, *args)
            ccode_conv = fld.ccode_convert(*args)
            conv_stat = c.Statement("%s *= %s" % (var, ccode_conv))
            cstat += [c.Assign("err", ccode_eval), conv_stat, c.Statement("CHECKSTATUS(err)")]
        node.ccode = c.Block(cstat)

    def visit_SummedVectorFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld, var, var2, var3 in zip(node.fields.obj, node.var, node.var2, node.var3):
            ccode_eval = fld.ccode_eval_array(var, var2, var3,
                                              fld.U, fld.V, fld.W, *args)
            if node.convert and fld.U.interp_method != 'cgrid_velocity':
                ccode_conv1 = fld.U.ccode_convert(*args)
                ccode_conv2 = fld.V.ccode_convert(*args)
                statements = [c.Statement("%s *= %s" % (var, ccode_conv1)),
                              c.Statement("%s *= %s" % (var2, ccode_conv2))]
            else:
                statements = []
            if node.convert and fld.vector_type == '3D':
                ccode_conv3 = fld.W.ccode_convert(*args)
                statements.append(c.Statement("%s *= %s" % (var3, ccode_conv3)))
            cstat += [c.Assign("err", ccode_eval), c.Block(statements)]
        cstat += [c.Statement("CHECKSTATUS(err)")]
        node.ccode = c.Block(cstat)

    def visit_NestedFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld in node.fields.obj:
            ccode_eval = fld.ccode_eval_array(node.var, *args)
            ccode_conv = fld.ccode_convert(*args)
            conv_stat = c.Statement("%s *= %s" % (node.var, ccode_conv))
            cstat += [c.Assign("err", ccode_eval),
                      conv_stat,
                      c.If("err != ERROR_OUT_OF_BOUNDS ", c.Block([c.Statement("CHECKSTATUS(err)"), c.Statement("break")]))]
        cstat += [c.Statement("CHECKSTATUS(err)"), c.Statement("break")]
        node.ccode = c.While("1==1", c.Block(cstat))

    def visit_NestedVectorFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld in node.fields.obj:
            ccode_eval = fld.ccode_eval_array(node.var, node.var2, node.var3,
                                              fld.U, fld.V, fld.W, *args)
            if fld.U.interp_method != 'cgrid_velocity':
                ccode_conv1 = fld.U.ccode_convert(*args)
                ccode_conv2 = fld.V.ccode_convert(*args)
                statements = [c.Statement("%s *= %s" % (node.var, ccode_conv1)),
                              c.Statement("%s *= %s" % (node.var2, ccode_conv2))]
            else:
                statements = []
            if fld.vector_type == '3D':
                ccode_conv3 = fld.W.ccode_convert(*args)
                statements.append(c.Statement("%s *= %s" % (node.var3, ccode_conv3)))
            cstat += [c.Assign("err", ccode_eval),
                      c.Block(statements),
                      c.If("err != ERROR_OUT_OF_BOUNDS ", c.Block([c.Statement("CHECKSTATUS(err)"), c.Statement("break")]))]
        cstat += [c.Statement("CHECKSTATUS(err)"), c.Statement("break")]
        node.ccode = c.While("1==1", c.Block(cstat))


class ObjectKernelGenerator(AbstractKernelGenerator):

    def __init__(self, fieldset=None, ptype=JITParticle):
        super(ObjectKernelGenerator, self).__init__(fieldset, ptype)

    @staticmethod
    def _check_FieldSamplingArguments(ccode):
        if ccode == 'particle':
            ccodes = ('time', 'particle->depth', 'particle->lat', 'particle->lon')
        elif ccode[-1] == 'particle':
            ccodes = ccode[:-1]
        else:
            ccodes = ccode
        return ccodes

    def visit_FunctionDef(self, node):
        # Generate "ccode" attribute by traversing the Python AST
        for stmt in node.body:
            if not (hasattr(stmt, 'value') and type(stmt.value) is ast.Str):  # ignore docstrings
                self.visit(stmt)

        # Create function declaration and argument list
        decl = c.Static(c.DeclSpecifier(c.Value("StatusCode", node.name), spec='inline'))
        args = [c.Pointer(c.Value(self.ptype.name, "particle")),
                c.Value("double", "time")]
        for field in self.field_args.values():
            args += [c.Pointer(c.Value("CField", "%s" % field.ccode_name))]
        for field in self.vector_field_args.values():
            for fcomponent in ['U', 'V', 'W']:
                try:
                    f = getattr(field, fcomponent)
                    if f.ccode_name not in self.field_args:
                        args += [c.Pointer(c.Value("CField", "%s" % f.ccode_name))]
                        self.field_args[f.ccode_name] = f
                except:
                    pass  # field.W does not always exist
        for const, _ in self.const_args.items():
            args += [c.Value("float", const)]

        # Create function body as C-code object
        body = [stmt.ccode for stmt in node.body if not (hasattr(stmt, 'value') and type(stmt.value) is ast.Str)]
        body += [c.Statement("return SUCCESS")]
        node.ccode = c.FunctionBody(c.FunctionDeclaration(decl, args), c.Block(body))

    def visit_FieldEvalNode(self, node):
        self.visit(node.field)
        self.visit(node.args)
        args = self._check_FieldSamplingArguments(node.args.ccode)
        ccode_eval = node.field.obj.ccode_eval_object(node.var, *args)
        stmts = [c.Assign("err", ccode_eval)]

        if node.convert:
            ccode_conv = node.field.obj.ccode_convert(*args)
            conv_stat = c.Statement("%s *= %s" % (node.var, ccode_conv))
            stmts += [conv_stat]

        node.ccode = c.Block(stmts + [c.Statement("CHECKSTATUS(err)")])

    def visit_VectorFieldEvalNode(self, node):
        self.visit(node.field)
        self.visit(node.args)
        args = self._check_FieldSamplingArguments(node.args.ccode)
        ccode_eval = node.field.obj.ccode_eval_object(node.var, node.var2, node.var3, node.field.obj.U, node.field.obj.V, node.field.obj.W, *args)
        if node.convert and node.field.obj.U.interp_method != 'cgrid_velocity':
            ccode_conv1 = node.field.obj.U.ccode_convert(*args)
            ccode_conv2 = node.field.obj.V.ccode_convert(*args)
            statements = [c.Statement("%s *= %s" % (node.var, ccode_conv1)),
                          c.Statement("%s *= %s" % (node.var2, ccode_conv2))]
        else:
            statements = []
        if node.convert and node.field.obj.vector_type == '3D':
            ccode_conv3 = node.field.obj.W.ccode_convert(*args)
            statements.append(c.Statement("%s *= %s" % (node.var3, ccode_conv3)))
        conv_stat = c.Block(statements)
        node.ccode = c.Block([c.Assign("err", ccode_eval),
                              conv_stat, c.Statement("CHECKSTATUS(err)")])

    def visit_SummedFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld, var in zip(node.fields.obj, node.var):
            ccode_eval = fld.ccode_eval_object(var, *args)
            ccode_conv = fld.ccode_convert(*args)
            conv_stat = c.Statement("%s *= %s" % (var, ccode_conv))
            cstat += [c.Assign("err", ccode_eval), conv_stat, c.Statement("CHECKSTATUS(err)")]
        node.ccode = c.Block(cstat)

    def visit_SummedVectorFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld, var, var2, var3 in zip(node.fields.obj, node.var, node.var2, node.var3):
            ccode_eval = fld.ccode_eval_object(var, var2, var3, fld.U, fld.V, fld.W, *args)
            if node.convert and fld.U.interp_method != 'cgrid_velocity':
                ccode_conv1 = fld.U.ccode_convert(*args)
                ccode_conv2 = fld.V.ccode_convert(*args)
                statements = [c.Statement("%s *= %s" % (var, ccode_conv1)),
                              c.Statement("%s *= %s" % (var2, ccode_conv2))]
            else:
                statements = []
            if node.convert and fld.vector_type == '3D':
                ccode_conv3 = fld.W.ccode_convert(*args)
                statements.append(c.Statement("%s *= %s" % (var3, ccode_conv3)))
            cstat += [c.Assign("err", ccode_eval), c.Block(statements)]
        cstat += [c.Statement("CHECKSTATUS(err)")]
        node.ccode = c.Block(cstat)

    def visit_NestedFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld in node.fields.obj:
            ccode_eval = fld.ccode_eval_object(node.var, *args)
            ccode_conv = fld.ccode_convert(*args)
            conv_stat = c.Statement("%s *= %s" % (node.var, ccode_conv))
            cstat += [c.Assign("err", ccode_eval),
                      conv_stat,
                      c.If("err != ERROR_OUT_OF_BOUNDS ", c.Block([c.Statement("CHECKSTATUS(err)"), c.Statement("break")]))]
        cstat += [c.Statement("CHECKSTATUS(err)"), c.Statement("break")]
        node.ccode = c.While("1==1", c.Block(cstat))

    def visit_NestedVectorFieldEvalNode(self, node):
        self.visit(node.fields)
        self.visit(node.args)
        cstat = []
        args = self._check_FieldSamplingArguments(node.args.ccode)
        for fld in node.fields.obj:
            ccode_eval = fld.ccode_eval_object(node.var, node.var2, node.var3, fld.U, fld.V, fld.W, *args)
            if fld.U.interp_method != 'cgrid_velocity':
                ccode_conv1 = fld.U.ccode_convert(*args)
                ccode_conv2 = fld.V.ccode_convert(*args)
                statements = [c.Statement("%s *= %s" % (node.var, ccode_conv1)),
                              c.Statement("%s *= %s" % (node.var2, ccode_conv2))]
            else:
                statements = []
            if fld.vector_type == '3D':
                ccode_conv3 = fld.W.ccode_convert(*args)
                statements.append(c.Statement("%s *= %s" % (node.var3, ccode_conv3)))
            cstat += [c.Assign("err", ccode_eval),
                      c.Block(statements),
                      c.If("err != ERROR_OUT_OF_BOUNDS ", c.Block([c.Statement("CHECKSTATUS(err)"), c.Statement("break")]))]
        cstat += [c.Statement("CHECKSTATUS(err)"), c.Statement("break")]
        node.ccode = c.While("1==1", c.Block(cstat))


class LoopGenerator(object):
    """Code generator class that adds type definitions and the outer
    loop around kernel functions to generate compilable C code."""

    def __init__(self, fieldset, ptype=None):
        self.fieldset = fieldset
        self.ptype = ptype

    def generate(self, funcname, field_args, const_args, kernel_ast, c_include):
        ccode = []

        pname = self.ptype.name + 'p'

        # ==== Add include for Parcels and math header ==== #
        ccode += [str(c.Include("parcels.h", system=False))]
        ccode += [str(c.Include("math.h", system=False))]
        ccode += [str(c.Assign('double _next_dt', '0'))]
        ccode += [str(c.Assign('size_t _next_dt_set', '0'))]
        ccode += [str(c.Assign('const int ngrid', str(self.fieldset.gridset.size if self.fieldset is not None else 1)))]

        # ==== Generate type definition for particle type ==== #
        vdeclp = [c.Pointer(c.POD(v.dtype, v.name)) for v in self.ptype.variables]
        ccode += [str(c.Typedef(c.GenerableStruct("", vdeclp, declname=pname)))]
        # ==== Generate type definition for single particle type ==== #
        vdecl = [c.POD(v.dtype, v.name) for v in self.ptype.variables if v.dtype != np.uint64]
        ccode += [str(c.Typedef(c.GenerableStruct("", vdecl, declname=self.ptype.name)))]

        args = [c.Pointer(c.Value(self.ptype.name, "particle_backup")),
                c.Pointer(c.Value(pname, "particles")),
                c.Value("int", "pnum")]
        p_back_set_decl = c.FunctionDeclaration(c.Static(c.DeclSpecifier(c.Value("void", "set_particle_backup"),
                                                         spec='inline')), args)
        body = []
        for v in self.ptype.variables:
            if v.dtype != np.uint64 and v.name not in ['dt', 'state']:
                body += [c.Assign(("particle_backup->%s" % v.name), ("particles->%s[pnum]" % v.name))]
        p_back_set_body = c.Block(body)
        p_back_set = str(c.FunctionBody(p_back_set_decl, p_back_set_body))
        ccode += [p_back_set]

        args = [c.Pointer(c.Value(self.ptype.name, "particle_backup")),
                c.Pointer(c.Value(pname, "particles")),
                c.Value("int", "pnum")]
        p_back_get_decl = c.FunctionDeclaration(c.Static(c.DeclSpecifier(c.Value("void", "get_particle_backup"),
                                                         spec='inline')), args)
        body = []
        for v in self.ptype.variables:
            if v.dtype != np.uint64 and v.name not in ['dt', 'state']:
                body += [c.Assign(("particles->%s[pnum]" % v.name), ("particle_backup->%s" % v.name))]
        p_back_get_body = c.Block(body)
        p_back_get = str(c.FunctionBody(p_back_get_decl, p_back_get_body))
        ccode += [p_back_get]

        update_next_dt_decl = c.FunctionDeclaration(c.Static(c.DeclSpecifier(c.Value("void", "update_next_dt"),
                                                             spec='inline')), [c.Value('double', 'dt')])
        if 'update_next_dt' in str(kernel_ast):
            body = []
            body += [c.Assign("_next_dt", "dt")]
            body += [c.Assign("_next_dt_set", "1")]
            update_next_dt_body = c.Block(body)
            update_next_dt = str(c.FunctionBody(update_next_dt_decl, update_next_dt_body))
            ccode += [update_next_dt]

        if c_include:
            ccode += [c_include]

        # ==== Insert kernel code ==== #
        ccode += [str(kernel_ast)]

        # Generate outer loop for repeated kernel invocation
        args = [c.Value("int", "num_particles"),
                c.Pointer(c.Value(pname, "particles")),
                c.Value("double", "endtime"), c.Value("double", "dt")]
        for field, _ in field_args.items():
            args += [c.Pointer(c.Value("CField", "%s" % field))]
        for const, _ in const_args.items():
            args += [c.Value("double", const)]  # are we SURE those const's are double's ?
        fargs_str = ", ".join(['particles->time[pnum]'] + list(field_args.keys())
                              + list(const_args.keys()))
        # ==== statement clusters use to compose 'body' variable and variables 'time_loop' and 'part_loop' ==== ##
        sign_dt = c.Assign("sign_dt", "dt > 0 ? 1 : -1")
        particle_backup = c.Statement("%s particle_backup" % self.ptype.name)
        sign_end_part = c.Assign("sign_end_part", "(endtime - particles->time[pnum]) > 0 ? 1 : -1")
        reset_res_state = c.Assign("res", "particles->state[pnum]")
        update_state = c.Assign("particles->state[pnum]", "res")
        update_pdt = c.If("_next_dt_set == 1",
                          c.Block([c.Assign("_next_dt_set", "0"), c.Assign("particles->dt[pnum]", "_next_dt")]))

        dt_pos = c.If("fabs(endtime - particles->time[pnum])<fabs(particles->dt[pnum])",
                      c.Block([c.Assign("__dt", "fabs(endtime - particles->time[pnum])"), c.Assign("reset_dt", "1")]),
                      c.Block([c.Assign("__dt", "fabs(particles->dt[pnum])"), c.Assign("reset_dt", "0")]))
        reset_dt = c.If("(reset_dt == 1) && is_equal_dbl(__pdt_prekernels, particles->dt[pnum])",
                        c.Block([c.Assign("particles->dt[pnum]", "dt")]))

        pdt_eq_dt_pos = c.Assign("__pdt_prekernels", "__dt * sign_dt")
        partdt = c.Assign("particles->dt[pnum]", "__pdt_prekernels")
        check_pdt = c.If("(res == SUCCESS) & !is_equal_dbl(__pdt_prekernels, particles->dt[pnum])", c.Assign("res", "REPEAT"))

        dt_0_break = c.If("is_zero_dbl(particles->dt[pnum])", c.Statement("break"))

        notstarted_continue = c.If("(( sign_end_part != sign_dt) || is_close_dbl(__dt, 0) ) && !is_zero_dbl(particles->dt[pnum])",
                                   c.Block([
                                       c.If("fabs(particles->time[pnum]) >= fabs(endtime)",
                                            c.Assign("particles->state[pnum]", "SUCCESS")),
                                       c.Statement("continue")
                                   ]))

        # ==== main computation body ==== #
        body = [c.Statement("set_particle_backup(&particle_backup, particles, pnum)")]
        body += [pdt_eq_dt_pos]
        body += [partdt]
        body += [c.Value("StatusCode", "state_prev"), c.Assign("state_prev", "particles->state[pnum]")]
        body += [c.Assign("res", "%s(particles, pnum, %s)" % (funcname, fargs_str))]
        body += [c.If("(res==SUCCESS) && (particles->state[pnum] != state_prev)", c.Assign("res", "particles->state[pnum]"))]
        body += [check_pdt]
        body += [c.If("res == SUCCESS || res == DELETE", c.Block([c.Statement("particles->time[pnum] += particles->dt[pnum]"),
                                                                  reset_dt,
                                                                  update_pdt,
                                                                  dt_pos,
                                                                  sign_end_part,
                                                                  c.If("(res != DELETE) && !is_close_dbl(__dt, 0) && (sign_dt == sign_end_part)",
                                                                       c.Assign("res", "EVALUATE")),
                                                                  c.If("sign_dt != sign_end_part", c.Assign("__dt", "0")),
                                                                  update_state,
                                                                  dt_0_break
                                                                  ]),
                      c.Block([c.Statement("get_particle_backup(&particle_backup, particles, pnum)"),
                               dt_pos,
                               sign_end_part,
                               c.If("sign_dt != sign_end_part", c.Assign("__dt", "0")),
                               update_state,
                               c.Statement("break")])
                      )]

        time_loop = c.While("(particles->state[pnum] == EVALUATE || particles->state[pnum] == REPEAT) || is_zero_dbl(particles->dt[pnum])", c.Block(body))
        part_loop = c.For("pnum = 0", "pnum < num_particles", "++pnum",
                          c.Block([sign_end_part, reset_res_state, dt_pos, notstarted_continue, time_loop]))
        fbody = c.Block([c.Value("int", "pnum, sign_dt, sign_end_part"),
                         c.Value("StatusCode", "res"),
                         c.Value("double", "reset_dt"),
                         c.Value("double", "__pdt_prekernels"),
                         c.Value("double", "__dt"),  # 1e-8 = built-in tolerance for np.isclose()
                         sign_dt, particle_backup, part_loop])
        fdecl = c.FunctionDeclaration(c.Value("void", "particle_loop"), args)
        ccode += [str(c.FunctionBody(fdecl, fbody))]
        return "\n\n".join(ccode)


class ParticleObjectLoopGenerator(object):
    """Code generator class that adds type definitions and the outer
    loop around kernel functions to generate compilable C code."""

    def __init__(self, fieldset=None, ptype=None):
        self.fieldset = fieldset
        self.ptype = ptype

    def generate(self, funcname, field_args, const_args, kernel_ast, c_include):
        ccode = []

        # ==== Add include for Parcels and math header ==== #
        ccode += [str(c.Include("parcels.h", system=False))]
        ccode += [str(c.Include("math.h", system=False))]
        ccode += [str(c.Assign('double _next_dt', '0'))]
        ccode += [str(c.Assign('size_t _next_dt_set', '0'))]
        ccode += [str(c.Assign('const int ngrid', str(self.fieldset.gridset.size if self.fieldset is not None else 1)))]

        # ==== Generate type definition for particle type ==== #
        vdecl = []
        for v in self.ptype.variables:
            if v.dtype == np.uint64:
                vdecl.append(c.Pointer(c.POD(np.void, v.name)))
            else:
                vdecl.append(c.POD(v.dtype, v.name))

        ccode += [str(c.Typedef(c.GenerableStruct("", vdecl, declname=self.ptype.name)))]

        args = [c.Pointer(c.Value(self.ptype.name, "particle_backup")),
                c.Pointer(c.Value(self.ptype.name, "particle"))]
        p_back_set_decl = c.FunctionDeclaration(c.Static(c.DeclSpecifier(c.Value("void", "set_particle_backup"),
                                                         spec='inline')), args)
        body = []
        for v in self.ptype.variables:
            if v.dtype != np.uint64 and v.name not in ['dt', 'state']:
                body += [c.Assign(("particle_backup->%s" % v.name), ("particle->%s" % v.name))]
        p_back_set_body = c.Block(body)
        p_back_set = str(c.FunctionBody(p_back_set_decl, p_back_set_body))
        ccode += [p_back_set]

        args = [c.Pointer(c.Value(self.ptype.name, "particle_backup")),
                c.Pointer(c.Value(self.ptype.name, "particle"))]
        p_back_get_decl = c.FunctionDeclaration(c.Static(c.DeclSpecifier(c.Value("void", "get_particle_backup"),
                                                         spec='inline')), args)
        body = []
        for v in self.ptype.variables:
            if v.dtype != np.uint64 and v.name not in ['dt', 'state']:
                body += [c.Assign(("particle->%s" % v.name), ("particle_backup->%s" % v.name))]
        p_back_get_body = c.Block(body)
        p_back_get = str(c.FunctionBody(p_back_get_decl, p_back_get_body))
        ccode += [p_back_get]

        update_next_dt_decl = c.FunctionDeclaration(c.Static(c.DeclSpecifier(c.Value("void", "update_next_dt"),
                                                             spec='inline')), [c.Value('double', 'dt')])
        if 'update_next_dt' in str(kernel_ast):
            body = []
            body += [c.Assign("_next_dt", "dt")]
            body += [c.Assign("_next_dt_set", "1")]
            update_next_dt_body = c.Block(body)
            update_next_dt = str(c.FunctionBody(update_next_dt_decl, update_next_dt_body))
            ccode += [update_next_dt]

        if c_include:
            ccode += [c_include]

        # ==== Insert kernel code ==== #
        ccode += [str(kernel_ast)]

        # Generate outer loop for repeated kernel invocation
        args = [c.Value("int", "num_particles"),
                c.Pointer(c.Value(self.ptype.name, "particles")),
                c.Value("double", "endtime"),
                c.Value("double", "dt")
                ]
        for field, _ in field_args.items():
            args += [c.Pointer(c.Value("CField", "%s" % field))]
        for const, _ in const_args.items():
            args += [c.Value("double", const)]  # are we SURE those const's are double's ?
        fargs_str = ", ".join(['particles[p].time'] + list(field_args.keys())
                              + list(const_args.keys()))
        # ==== statement clusters use to compose 'body' variable and variables 'time_loop' and 'part_loop' ==== ##
        sign_dt = c.Assign("sign_dt", "dt > 0 ? 1 : -1")
        particle_backup = c.Statement("%s particle_backup" % self.ptype.name)
        sign_end_part = c.Assign("sign_end_part", "(endtime - particles[p].time) > 0 ? 1 : -1")
        reset_res_state = c.Assign("res", "particles[p].state")
        update_state = c.Assign("particles[p].state", "res")
        update_pdt = c.If("_next_dt_set == 1",
                          c.Block([c.Assign("_next_dt_set", "0"), c.Assign("particles[p].dt", "_next_dt")]))

        dt_pos = c.If("fabs(endtime - particles[p].time) < fabs(particles[p].dt)",
                      c.Block([c.Assign("__dt", "fabs(endtime - particles[p].time)"), c.Assign("reset_dt", "1")]),
                      c.Block([c.Assign("__dt", "fabs(particles[p].dt)"), c.Assign("reset_dt", "0")]))
        reset_dt = c.If("(reset_dt == 1) && is_equal_dbl(__pdt_prekernels, particles[p].dt)",
                        c.Block([c.Assign("particles[p].dt", "dt")]))

        pdt_eq_dt_pos = c.Assign("__pdt_prekernels", "__dt * sign_dt")
        partdt = c.Assign("particles[p].dt", "__pdt_prekernels")
        check_pdt = c.If("(res == SUCCESS) & !is_equal_dbl(__pdt_prekernels, particles[p].dt)", c.Assign("res", "REPEAT"))

        dt_0_break = c.If("is_zero_dbl(particles[p].dt)", c.Statement("break"))

        notstarted_continue = c.If("( ( sign_end_part != sign_dt) || is_close_dbl(__dt, 0) ) && !is_zero_dbl(particles[p].dt)",
                                   c.Block([
                                       c.If("fabs(particles[p].time) >= fabs(endtime)",
                                            c.Assign("particles[p].state", "SUCCESS")),
                                       c.Statement("continue")
                                   ]))

        # ==== main computation body ==== #
        body = [c.Statement("set_particle_backup(&particle_backup, &(particles[p]))")]
        body += [pdt_eq_dt_pos]
        body += [partdt]
        body += [c.Value("StatusCode", "state_prev"), c.Assign("state_prev", "particles[p].state")]
        body += [c.Assign("res", "%s(&(particles[p]), %s)" % (funcname, fargs_str))]
        body += [c.If("(res == SUCCESS) && (particles[p].state != state_prev)", c.Assign("res", "particles[p].state"))]
        body += [check_pdt]
        body += [c.If("res == SUCCESS || res == DELETE", c.Block([c.Statement("particles[p].time += particles[p].dt"),
                                                                  reset_dt,
                                                                  update_pdt,
                                                                  dt_pos,
                                                                  sign_end_part,
                                                                  c.If("(res != DELETE) && !is_close_dbl(__dt, 0) && (sign_dt == sign_end_part)",
                                                                       c.Assign("res", "EVALUATE")),
                                                                  c.If("sign_dt != sign_end_part", c.Assign("__dt", "0")),
                                                                  update_state,
                                                                  dt_0_break
                                                                  ]),
                      c.Block([c.Statement("get_particle_backup(&particle_backup, &(particles[p]))"),
                               dt_pos,
                               sign_end_part,
                               c.If("sign_dt != sign_end_part", c.Assign("__dt", "0")),
                               update_state,
                               c.Statement("break")])
                      )]

        time_loop = c.While("(particles[p].state == EVALUATE || particles[p].state == REPEAT) || is_zero_dbl(particles[p].dt)", c.Block(body))
        part_loop = c.For("p = 0", "p < num_particles", "++p",
                          c.Block([sign_end_part, reset_res_state, dt_pos, notstarted_continue, time_loop]))
        fbody = c.Block([c.Value("int", "p, sign_dt, sign_end_part"),
                         c.Value("StatusCode", "res"),
                         c.Value("int", "reset_dt"),
                         c.Value("double", "__pdt_prekernels"),
                         c.Value("double", "__dt"),  # 1e-8 = built-in tolerance for np.isclose()
                         sign_dt, particle_backup, part_loop])
        fdecl = c.FunctionDeclaration(c.Value("void", "particle_loop"), args)
        ccode += [str(c.FunctionBody(fdecl, fbody))]
        return "\n\n".join(ccode)
