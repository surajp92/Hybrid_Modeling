#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 17 14:32:53 2019

@author: sayin
"""

from sympy import *
from sympy import init_printing
init_printing() # doctest: +SKIP
#from sympy.abc import nu

import dill
dill.settings['recurse'] = True

init_printing()

#%%
x,  t = symbols('x  t ')
a, nu = symbols('a nu ')

t0 = exp(1.0/(8.0*nu))
burg = (x*(-x)/(t+1.0))/(1.0+ sqrt((t+1.0)/t0)*exp(x*x/(4.0*nu*(t+1.0))))

burg


#%%
d1 = diff(burg, x)

d2 = diff(burg,x,2)

dt  = diff(burg,t,1)

res = simplify(dt + burg*d1 - nu*d2)

#%%
#print(pretty(res))

import pickle
with open("y.txt", "w") as outf:
    pickle.dump(str(res), outf)