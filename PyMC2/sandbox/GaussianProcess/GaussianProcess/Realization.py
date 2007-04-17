# TODO: When you use the one-at-a-time style, each point has to compute its M and C
# TODO: based on EACH OTHER POINT. So you need to store dev_sofar with that in mind.
# TODO: Two options: Either store each evaluation vector as a chunk and curse over
# TODO: the chunks, or do EVERYTHING one-at-a-time. Recommend the former.

# TODO: Check in __call__ if this argument is already in mesh so far. If so, skip
# TODO: the recompute. Also, it's too slow! Speed it up.

__docformat__='reStructuredText'

"""
Indexable and callable. Values on base mesh ('static') must always exist,
indexing returns one of those. Also keeps two nodes and an array:

"""

from numpy import *
from numpy.random import normal
from numpy.linalg import cholesky, eigh, solve
from Covariance import Covariance
from Mean import Mean
from GPutils import regularize_array, enlarge_chol, robust_chol, downdate, gentle_trisolve, fragile_chol

class Realization(ndarray):
    
    """
    f = Realization(M, C[, init_base_array])
    
    A realization from a Gaussian process. It's indexable and callable.
    
    :Arguments:
        - M: A Gaussian process mean function.
        - C: A Gaussian process covariance function.
        - init_base_array:  An optional ndarray giving the value of f over its base mesh (got from C). If no value is given, f's value over its base mesh is sampled given M and C.
                        
    :SeeAlso:
    GPMean, GPCovariance, GaussianProcess, condition
    """
    
    cov_fun = None
    mean_fun = None
                
    cov_params = None
    mean_params = None
    base_mesh = None
    base_reshape = None

    ndim = None
    C = None
    M = None

    N_obs_sofar = 0
    obs_mesh_sofar = None
    dev_sofar = None
    M_sofar = None
    chol_sofar = None
    
    __array_priority__ = 0.

    def __new__(subtype, 
                M,
                C,
                init_base_array = None):
        
        # You may need to reshape these so f2py doesn't puke.           
        base_mesh = C.base_mesh
        base_reshape = C.base_reshape
        cov_params = C.params
        mean_params = M.mean_params
        
        ndim = C.ndim
            
        if base_mesh is not None:
            length = base_reshape.shape[0]
            obs_mesh_sofar = base_reshape
            M_sofar = M.ravel()
            
            chol_sofar = C.S
            
            N_obs_sofar = length
            
            if init_base_array is not None:
                # If the value over the base array is specified, check it out.
                
                if not init_base_array.shape == M.shape:
                    raise ValueErrror, 'Argument init_base_array must be same shape as M.'

                f = init_base_array.view(subtype)

            else:
                # Otherwise, draw a value over the base array.

                q=reshape(asarray(normal(size = length) * C.S), base_mesh.shape)
                f = (M+q).view(subtype)
        else:
            f = array([]).view(subtype)
            base_reshape = array([])
            obs_mesh_sofar = None
            M_sofar = None
            chol_sofar = None
            N_obs_sofar = 0
        
        f.obs_mesh_sofar = obs_mesh_sofar
        f.M_sofar = M_sofar
        f.chol_sofar = chol_sofar
        f.N_obs_sofar = N_obs_sofar
        f.base_mesh = base_mesh
        f.cov_params = cov_params
        f.mean_params = mean_params
        f.base_reshape = base_reshape        
        f.cov_fun = C.eval_fun
        f.C = C
        f.mean_fun = M.eval_fun
        f.M = M
        f.ndim = C.ndim
        f.dev_sofar = f.view(ndarray).ravel() - f.M_sofar
        
        return f
        
    def __copy__(self, order='C'):

        f = self.view(ndarray).copy().view(Realization)

        f.cov_fun = self.cov_fun
        f.mean_fun = self.mean_fun

        f.cov_params = self.cov_params
        f.mean_params = self.mean_params
        f.base_mesh = self.base_mesh
        f.base_reshape = self.base_reshape

        f.ndim = self.ndim
        f.C = self.C
        f.M = self.M

        f.N_obs_sofar = self.N_obs_sofar
        f.obs_mesh_sofar = self.obs_mesh_sofar
        f.dev_sofar = self.dev_sofar
        f.M_sofar = self.M_sofar
        f.chol_sofar = self.chol_sofar

        return f

    def copy(self, order='C'):
        return self.__copy__()        
            
    def __call__(self, x):

        orig_shape = shape(x)
        x=regularize_array(x)
        xndim = x.shape[-1]
        
        # Either compare x's number of dimensions to self's number of dimensions,
        # or else set self's number of dimensions to that of x.
        if self.ndim is not None:
            if not xndim == self.ndim:
                raise ValueError, "The last dimension of x (the number of spatial dimensions) must be the same as self's ndim."
        else:
            self.ndim = xndim
            
        x = x.reshape(-1,self.ndim)
        lenx = x.shape[0]
        
        M_pure = self.M(x).ravel()
        C_pure = self.C(x)

        chol_now = fragile_chol(C_pure)
        M_now = M_pure.copy()
        
        # print self.N_obs_sofar
        # First observation:
        if self.N_obs_sofar == 0:
            self.M_sofar = M_now
            self.chol_sofar = chol_now
            self.obs_mesh_sofar = x

        # Subsequent observations:    
        else:    

            # Iterative conditioning may be better for this, but it is probably not:

            RF = self.C(x,self.obs_mesh_sofar)
            
            # TODO: The local Q needs to be Cholesky factorized by Bach and Jordan's method each time a
            # new observation comes in, unless you figure out something better. Actually, something better
            # should be fairly easily available... but Bach and Jordan may be faster for big observation
            # vectors.
            downdate(chol_now, RF, self.chol_sofar)

            term_1=gentle_trisolve(self.chol_sofar.T, RF.T)
            term_2=gentle_trisolve(self.chol_sofar, self.dev_sofar)

            M_now += asarray(term_1.T * term_2.T).ravel()

            self.obs_mesh_sofar = concatenate((self.obs_mesh_sofar, x), axis=0)
            self.M_sofar = concatenate((self.M_sofar, M_pure), axis=0)
            self.chol_sofar = enlarge_chol(self.chol_sofar, RF, C_pure)
            
        
        f = M_now + asarray(normal(size=lenx) * chol_now).ravel()

        
        if self.N_obs_sofar > 0:
            self.dev_sofar = concatenate((self.dev_sofar, f - M_pure), axis=0)
        else:
            self.dev_sofar = f - M_pure
            
        self.N_obs_sofar += lenx
        
        return f.reshape(orig_shape)                

    def __repr__(self):
        return object.__repr__(self)
        
    def __str__(self):
        # Thanks to the author of ma.array for this code.
        s = repr(self.__array__()).replace('array', 'array aspect: ')

        l = s.splitlines()
        for i in range(1, len(l)):
            if l[i]:
                l[i] = ' '*9 + l[i]
        array_part = '\n'.join(l)

        mean_fun_part = 'Associated mean function: ' + str(self.mean_fun)
        cov_fun_part = 'Associated covariance function: ' + str(self.cov_fun)
        obs_part = 'Number of evaluations so far: %i' % self.N_obs_sofar

        return '\n'.join(['Gaussian process realization',mean_fun_part,cov_fun_part,obs_part,array_part])
                
    def __getitem__(self, *args):
        return self.view(ndarray).__getitem__(*args)

    def __getslice__(self, *args):
        return self.view(ndarray).__getslice__(*args)        
        
    def __array_wrap__(self, array_in):
        return array_in.view(ndarray)
        

    def plot(self, mesh=None):
        from pylab import plot
        if self.ndim==1:
            plot(self.base_mesh, self.view(ndarray))
        elif self.ndim==2:
            contourf(self.base_mesh[:,0], self.base_mesh[:,1],self.view(ndarray))
            
#
# The Gaussian Process parameter class, if PyMC2 is installed
#
try:
    from PyMC2 import Parameter
    from GPutils import GP_logp

    class GaussianProcess(Parameter):
        """
        A subclass of Parameter that is valued as a Gaussian process Realization
        object.

        Instantiating this class with

        >>> f = GaussianProcess(M, C, name='f')

        is equivalent to the following:

        @parameter
        def f(value=Realization(M.value,C.value), M=M, C=C):

            def logp(value, M, C):
                return GP_logp(value,M,C)

            def random(M, C):
                return Realization(M,C)

            rseed =  1.

        """
        
        
        def __init__(self, M, C, name='f', doc="A Gaussian process", trace=True, rseed=1., isdata=False, cache_depth=2):
            
            self.M = M
            self.C = C
            
            def logp_fun(value, M, C):
                # TODO: Catch the error, change it to a ZeroProbability. DON'T handle the error
                # in the new R Q.I R.T function, you'll screw up the cache checking.
                return GP_logp(value, M, C)
                
            def random_fun(M, C):
                return Realization(M, C)
            
            Parameter.__init__( self, 
                                logp = logp_fun,
                                doc=doc, 
                                name=name, 
                                parents = {'M': M, 'C': C}, 
                                random = random_fun, 
                                trace = trace, 
                                value = Realization(M.value, C.value), 
                                rseed = rseed, 
                                isdata = isdata,
                                cache_depth=cache_depth)
                                
except ImportError:
    class GaussianProcess(object):
        def __init__(self, *args, **kwargs):
            raise ImportError, 'You must install PyMC to use the Gaussian process object.'
