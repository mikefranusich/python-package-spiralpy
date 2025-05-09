# spiralpy/mdrconvsolver.py
#
# Copyright 2018-2023, Carnegie Mellon University
# All rights reserved.
#
# See LICENSE (https://github.com/spiral-software/python-package-spiralpy/blob/main/LICENSE)

"""
SpiralPy Mdrconvsolver Module
==============================

Cyclic 3D Real Cyclic Convolution
"""

from spiralpy import *
from spiralpy.spsolver import *
import numpy as np
import ctypes
import sys

try:
    import cupy as cp
except ModuleNotFoundError:
    cp = None

class MdrconvProblem(SPProblem):
    """define cyclic convolution problem."""

    def __init__(self, ns):
        """Setup problem specifics for Mdrconv solver.
        
        Arguments:
        ns      -- shape (list) of MDRCONV box of reals
        """
        super(MdrconvProblem, self).__init__(ns)

    def dimN(self):
        return self.dimensions()[0]


class MdrconvSolver(SPSolver):
    def __init__(self, problem: MdrconvProblem, opts = {}):
        if not isinstance(problem, MdrconvProblem):
            raise TypeError("problem must be an MdrconvProblem")
        
        typ = 'd'
        self._ftype = np.double
        self._cxtype = np.cdouble
        if opts.get(SP_OPT_REALCTYPE, 0) == 'float':
            typ = 'f'
            self._ftype = np.single
            self._cxtype = np.csingle
        
        ns = 'x'.join([str(n) for n in problem.dimensions()])
        namebase = typ + 'Mdrconv_' + ns

        opts[SP_OPT_METADATA] = True

        super(MdrconvSolver, self).__init__(problem, namebase, opts)


    def _trace(self):
        """Trace execution for generating Spiral script"""
        self._tracingOn = True
        self._callGraph = []
        (src,sym) = self.buildTestInput()
        self.runDef(src,sym)
        self._tracingOn = False
        for i in range(len(self._callGraph)-1):
            self._callGraph[i] = self._callGraph[i] + ','
            
    def runDef(self, src, sym):
        """Solve using internal Python definition."""
        
        srcF = self.rfftn(src)
        P = self.pointwise(srcF, sym)
        out = self.irfftn(P, shape=src.shape)
        
        return out
    
    def solve(self, src, sym, dst=None):
        """Call SPIRAL-generated code"""
        
        xp = sp.get_array_module(src)
        
        #slice sym if it's a cube
        shape = sym.shape
        if shape[0] == shape[2]:
            N = shape[0]
            Nx = (N // 2) + 1
            sym = xp.ascontiguousarray(sym[:, :, :Nx])
                
        n1 = self._problem.dimensions()[0]
        n2 = self._problem.dimensions()[1]
        n3 = self._problem.dimensions()[2]
        if type(dst) == type(None):
            dst = xp.zeros((n1,n2,n3), src.dtype)
        self._func(dst, src, sym)
        xp.divide(dst, n1*n2*n3, out=dst)
        return dst
 
    def _func(self, dst, src, sym):
        """Call the SPIRAL generated main function"""
                
        xp = sp.get_array_module(src)
        
        if xp == np: 
            if self._genCuda or self._genHIP:
                raise RuntimeError('GPU function requires CuPy arrays')
            # NumPy array on CPU
            return self._MainFunc( 
                    dst.ctypes.data_as(ctypes.c_void_p),
                    src.ctypes.data_as(ctypes.c_void_p),
                    sym.ctypes.data_as(ctypes.c_void_p)  )
        else:
            if not self._genCuda and not self._genHIP:
                raise RuntimeError('CPU function requires NumPy arrays')
            # CuPy array on GPU
            dstdev = ctypes.cast(dst.data.ptr, ctypes.POINTER(ctypes.c_void_p))
            srcdev = ctypes.cast(src.data.ptr, ctypes.POINTER(ctypes.c_void_p))
            symdev = ctypes.cast(sym.data.ptr, ctypes.POINTER(ctypes.c_void_p))
            return self._MainFunc(dstdev, srcdev, symdev)
  

    def _writeScript(self, script_file):
        nameroot = self._namebase
        filename = nameroot
        filetype = '.c'
        if self._genCuda:
            filetype = '.cu'
        if self._genHIP:
            filetype = '.cpp'
        
        print("Load(fftx);", file = script_file)
        print("ImportAll(fftx);", file = script_file)
        print("", file = script_file)
        if self._genCuda:
            print("conf := LocalConfig.fftx.confGPU();", file = script_file)
        elif self._genHIP:
            print ( 'conf := FFTXGlobals.defaultHIPConf();', file = script_file )
        else:
            print("conf := LocalConfig.fftx.defaultConf();", file = script_file)

        print("", file = script_file)
        print('t := let(symvar := var("sym", TPtr(TReal)),', file = script_file)
        print("    TFCall(", file = script_file)
        print("        Compose([", file = script_file)
        for i in range(len(self._callGraph)):
            print("            " + self._callGraph[i], file = script_file)
        print("        ]),", file = script_file)
        print('        rec(fname := "' + nameroot + '", params := [symvar])', file = script_file)
        print("    )", file = script_file)
        print(");", file = script_file)
        print("", file = script_file)
        print("opts := conf.getOpts(t);", file = script_file)

        if self._genCuda or self._genHIP:
            print('opts.wrapCFuncs := true;', file = script_file)

        if self._opts.get(SP_OPT_REALCTYPE) == "float":
            print('opts.TRealCtype := "float";', file = script_file)

        if self._printRuleTree:
            print("opts.printRuleTree := true;", file = script_file)

        print("tt := opts.tagIt(t);", file = script_file)
        print("", file = script_file)
        print("c := opts.fftxGen(tt);", file = script_file)
        print('PrintTo("' + filename + filetype + '", opts.prettyPrint(c));', file = script_file)
        print("", file = script_file)
    
    def buildTestInput(self):
        """ Build test input cube """
        
        xp = cp if self._genCuda or self._genHIP else np
        n1 = self._problem.dimensions()[0]
        n2 = self._problem.dimensions()[1]
        n3 = self._problem.dimensions()[2]
        
        testSrc = xp.random.rand(n1,n2,n3).astype(self._ftype)
        
        symIn = xp.random.rand(n1,n2,n3).astype(self._ftype)
        testSym = xp.fft.rfftn(symIn)
        
        #NumPy returns Fortran ordering from FFTs, and always double complex
        if xp == np:
            testSym = np.asanyarray(testSym, dtype=self._cxtype, order='C')
        
        return (testSrc, testSym)
    
    def _setFunctionMetadata(self, obj):
        obj[SP_KEY_TRANSFORMTYPE] = SP_TRANSFORM_MDRCONV
     

    
