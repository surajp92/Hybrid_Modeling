# -*- coding: utf-8 -*-
"""
Created on Mon Oct  7 17:41:10 2019

@author: Shady
"""
#%% Import libraries
import numpy as np
import matplotlib.pyplot as plt
from numpy import linalg as LA
from scipy.integrate import simps
import pyfftw

from numpy.random import seed
seed(1)
from tensorflow import set_random_seed
set_random_seed(2)
import pandas as pd
import time as clck
import os

from keras.models import Sequential
from keras.layers import Dense
from keras.layers import LSTM
from keras.models import load_model
import keras.backend as K
K.set_floatx('float64')

from sklearn.preprocessing import MinMaxScaler
from sklearn.externals import joblib
from sklearn.model_selection import train_test_split

from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.axes_grid1 import make_axes_locatable
from matplotlib import ticker

font = {'family' : 'Times New Roman',
        'weight' : 'bold',
        'size'   : 14}    
plt.rc('font', **font)

import matplotlib as mpl
mpl.rcParams['text.usetex'] = True
mpl.rcParams['text.latex.preamble'] = [r'\usepackage{amsmath}']


#%% Define Functions

###############################################################################
#POD Routines
###############################################################################         
def POD(u,R): #Basis Construction
    n,ns = u.shape
    U,S,Vh = LA.svd(u, full_matrices=False)
    Phi = U[:,:R]  
    L = S**2
    #compute RIC (relative inportance index)
    RIC = sum(L[:R])/sum(L)*100   
    return Phi,L,RIC

def PODproj(u,Phi): #Projection
    a = np.dot(u.T,Phi)  # u = Phi * a.T
    return a

def PODrec(a,Phi): #Reconstruction    
    u = np.dot(Phi,a.T)    
    return u


###############################################################################
#Interpolation Routines
###############################################################################  
# Grassmann Interpolation
def GrassInt(Phi,pref,p,pTest):
    # Phi is input basis [training]
    # pref is the reference basis [arbitrarty] for Grassmann interpolation
    # p is the set of training parameters
    # pTest is the testing parameter
    
    nx,nr,nc = Phi.shape
    Phi0 = Phi[:,:,pref] 
    Phi0H = Phi0.T 

    print('Calculating Gammas...')
    Gamma = np.zeros((nx,nr,nc))
    for i in range(nc):
        templ = ( np.identity(nx) - Phi0.dot(Phi0H) )
        tempr = LA.inv( Phi0H.dot(Phi[:,:,i]) )
        temp = LA.multi_dot([templ,Phi[:,:,i],tempr])
               
        U, S, Vh = LA.svd(temp, full_matrices=False)
        S = np.diag(S)
        Gamma[:,:,i] = LA.multi_dot([U,np.arctan(S),Vh])
    
    print('Interpolating ...')
    alpha = np.ones(nc)
    GammaL = np.zeros((nx,nr))
    #% Lagrange Interpolation
    for i in range(nc):
        for j in range(nc):
            if (j != i) :
                alpha[i] = alpha[i]*(pTest-p[j])/(p[i]-p[j])
    for i in range(nc):
        GammaL = GammaL + alpha[i] * Gamma[:,:,i]
            
    U, S, Vh = LA.svd(GammaL, full_matrices=False)
    PhiL = LA.multi_dot([ Phi0 , Vh.T ,np.diag(np.cos(S)) ]) + \
           LA.multi_dot([ U , np.diag(np.sin(S)) ])
    PhiL = PhiL.dot(Vh)
    return PhiL

###############################################################################
#LSTM Routines
############################################################################### 
def create_training_data_lstm(training_set, m, n, lookback):
    ytrain = [training_set[i,:] for i in range(lookback,m)]
    ytrain = np.array(ytrain)    
    xtrain = np.zeros((m-lookback,lookback,n))
    for i in range(m-lookback):
        a = training_set[i,:]
        for j in range(1,lookback):
            a = np.vstack((a,training_set[i+j,:]))
        xtrain[i,:,:] = a
    return xtrain , ytrain



def rhs(nr, b_l, b_nl, a): # Right Handside of Galerkin Projection
    r2, r3, r = [np.zeros(nr) for _ in range(3)]
    
    for k in range(nr):
        r2[k] = 0.0
        for i in range(nr):
            r2[k] = r2[k] + b_l[i,k]*a[i]
    
    for k in range(nr):
        r3[k] = 0.0
        for j in range(nr):
            for i in range(nr):
                r3[k] = r3[k] + b_nl[i,j,k]*a[i]*a[j]
    
    r = r2 + r3    
    return r

def plot_3d_surface(x,t,field):
    
    fig = plt.figure(figsize=(8,6))
    ax = fig.add_subplot(111, projection='3d')
    
    X, Y = np.meshgrid(t, x)
    
    surf = ax.plot_surface(Y, X, field, cmap=plt.cm.viridis,
                           linewidth=1, antialiased=False,rstride=1,
                            cstride=1)
    
    fig.colorbar(surf, shrink=0.5, aspect=5)
    
    fig.tight_layout()
    plt.show()
    fig.savefig('3d.pdf')

#%% fast poisson solver using second-order central difference scheme
def fpsi(nx, ny, dx, dy, f):
    epsilon = 1.0e-6
    aa = -2.0/(dx*dx) - 2.0/(dy*dy)
    bb = 2.0/(dx*dx)
    cc = 2.0/(dy*dy)
    hx = 2.0*np.pi/np.float64(nx)
    hy = 2.0*np.pi/np.float64(ny)
    
    kx = np.empty(nx)
    ky = np.empty(ny)
    
    kx[:] = hx*np.float64(np.arange(0, nx))

    ky[:] = hy*np.float64(np.arange(0, ny))
    
    kx[0] = epsilon
    ky[0] = epsilon

    kx, ky = np.meshgrid(np.cos(kx), np.cos(ky), indexing='ij')
    
    data = np.empty((nx,ny), dtype='complex128')
    data1 = np.empty((nx,ny), dtype='complex128')
    
    data[:,:] = np.vectorize(complex)(f,0.0)

    a = pyfftw.empty_aligned((nx,ny),dtype= 'complex128')
    b = pyfftw.empty_aligned((nx,ny),dtype= 'complex128')
    
    fft_object = pyfftw.FFTW(a, b, axes = (0,1), direction = 'FFTW_FORWARD')
    fft_object_inv = pyfftw.FFTW(a, b,axes = (0,1), direction = 'FFTW_BACKWARD')
    
    e = fft_object(data)
    #e = pyfftw.interfaces.scipy_fftpack.fft2(data)
    
    e[0,0] = 0.0
    
    data1[:,:] = e[:,:]/(aa + bb*kx[:,:] + cc*ky[:,:])

    ut = np.real(fft_object_inv(data1))
        
    return ut

#%%
def nonlinear_term(nx,ny,dx,dy,wf,sf):
    '''
    this function returns -(Jacobian)
    
    '''
    w = np.zeros((nx+3,ny+3))
    
    w[1:nx+1,1:ny+1] = wf
    
    # periodic
    w[:,ny+1] = w[:,1]
    w[nx+1,:] = w[1,:]
    w[nx+1,ny+1] = w[1,1]
    
    # ghost points
    w[:,0] = w[:,ny]
    w[:,ny+2] = w[:,2]
    w[0,:] = w[nx,:]
    w[nx+2,:] = w[2,:]
    
    s = np.zeros((nx+3,ny+3))
    
    s[1:nx+1,1:ny+1] = sf
    
    # periodic
    s[:,ny+1] = s[:,1]
    s[nx+1,:] = s[1,:]
    s[nx+1,ny+1] = s[1,1]
    
    # ghost points
    s[:,0] = s[:,ny]
    s[:,ny+2] = s[:,2]
    s[0,:] = s[nx,:]
    s[nx+2,:] = s[2,:]
    
    gg = 1.0/(4.0*dx*dy)
    hh = 1.0/3.0
    
    f = np.zeros((nx+1,ny+1))
    
    #Arakawa
    j1 = gg*( (w[2:nx+3,1:ny+2]-w[0:nx+1,1:ny+2])*(s[1:nx+2,2:ny+3]-s[1:nx+2,0:ny+1]) \
             -(w[1:nx+2,2:ny+3]-w[1:nx+2,0:ny+1])*(s[2:nx+3,1:ny+2]-s[0:nx+1,1:ny+2]))

    j2 = gg*( w[2:nx+3,1:ny+2]*(s[2:nx+3,2:ny+3]-s[2:nx+3,0:ny+1]) \
            - w[0:nx+1,1:ny+2]*(s[0:nx+1,2:ny+3]-s[0:nx+1,0:ny+1]) \
            - w[1:nx+2,2:ny+3]*(s[2:nx+3,2:ny+3]-s[0:nx+1,2:ny+3]) \
            + w[1:nx+2,0:ny+1]*(s[2:nx+3,0:ny+1]-s[0:nx+1,0:ny+1]))

    j3 = gg*( w[2:nx+3,2:ny+3]*(s[1:nx+2,2:ny+3]-s[2:nx+3,1:ny+2]) \
            - w[0:nx+1,0:ny+1]*(s[0:nx+1,1:ny+2]-s[1:nx+2,0:ny+1]) \
            - w[0:nx+1,2:ny+3]*(s[1:nx+2,2:ny+3]-s[0:nx+1,1:ny+2]) \
            + w[2:nx+3,0:ny+1]*(s[2:nx+3,1:ny+2]-s[1:nx+2,0:ny+1]) )

    f = -(j1+j2+j3)*hh
                  
    return f[1:nx+1,1:ny+1]

def linear_term(nx,ny,dx,dy,re,f):
    w = np.zeros((nx+3,ny+3))
    
    w[1:nx+1,1:ny+1] = f
    
    # periodic
    w[:,ny+1] = w[:,1]
    w[nx+1,:] = w[1,:]
    w[nx+1,ny+1] = w[1,1]
    
    # ghost points
    w[:,0] = w[:,ny]
    w[:,ny+2] = w[:,2]
    w[0,:] = w[nx,:]
    w[nx+2,:] = w[2,:]
    
    aa = 1.0/(dx*dx)
    bb = 1.0/(dy*dy)
    
    f = np.zeros((nx+1,ny+1))
    
    lap = aa*(w[2:nx+3,1:ny+2]-2.0*w[1:nx+2,1:ny+2]+w[0:nx+1,1:ny+2]) \
        + bb*(w[1:nx+2,2:ny+3]-2.0*w[1:nx+2,1:ny+2]+w[1:nx+2,0:ny+1])
    
    f = lap/re
            
    return f[1:nx+1,1:ny+1]

def pbc(w):
    f = np.zeros((nx+1,ny+1))
    f[:nx,:ny] = w
    f[:,ny] = f[:,0]
    f[nx,:] = f[0,:]
    
    return f

#%% Main program:
# Inputs
nx =  128  #spatial grid number
ny = 128
nc = 4     #number of control parameters (nu)
ns = 200    #number of snapshot per each Parameter 
nr = 8      #number of modes
Re_start = 200.0
Re_final = 800.0
Re  = np.linspace(Re_start, Re_final, nc) #control Reynolds
nu = 1/Re   #control dissipation
lx = 2.0*np.pi
ly = 2.0*np.pi
dx = lx/nx
dy = ly/ny
dt = 1e-1
tm = 20.0

noise = 0.3

ReTest = 1000

#%% Data generation for training
x = np.linspace(0, lx, nx+1)
y = np.linspace(0, ly, ny+1)
t = np.linspace(0, tm, ns+1)

um = np.zeros(((nx)*(ny), ns+1, nc))
up = np.zeros(((nx)*(ny), ns+1, nc))
uo = np.zeros(((nx)*(ny), ns+1, nc))

for p in range(0,nc):
    for n in range(0,ns+1):
        file_input = "./snapshots/Re_"+str(int(Re[p]))+"/w/w_"+str(int(n))+ ".csv"
        w = np.genfromtxt(file_input, delimiter=',')
        
        w1 = w[1:nx+1,1:ny+1]
        
        um[:,n,p] = np.reshape(w1,(nx)*(ny)) #snapshots from unperturbed solution
        up[:,n,p] = noise*um[:,n,p] #perturbation (unknown physics)
        uo[:,n,p] = um[:,n,p] + up[:,n,p] #snapshots from observed solution

#plot_3d_surface(x,t,uo[:,:,-1])

#%% POD basis computation
PHIw = np.zeros(((nx)*(ny),nr,nc))
PHIs = np.zeros(((nx)*(ny),nr,nc))        
       
L = np.zeros((ns+1,nc)) #Eigenvalues      
RIC = np.zeros((nc))    #Relative information content

print('Computing POD basis for vorticity ...')
for p in range(0,nc):
    u = uo[:,:,p]
    PHIw[:,:,p], L[:,p], RIC[p]  = POD(u, nr) 

#%%    
print('Computing POD basis for streamfunction ...')
for p in range(0,nc):
    for i in range(nr):
        phi_w = np.reshape(PHIw[:,i,p],[nx,ny])
        phi_s = fpsi(nx, ny, dx, dy, -phi_w)
        PHIs[:,i,p] = np.reshape(phi_s,(nx)*(ny))

#%% Calculating true POD coefficients (observed)
at = np.zeros((ns+1,nr,nc))
print('Computing true POD coefficients...')
for p in range(nc):
    at[:,:,p] = PODproj(uo[:,:,p],PHIw[:,:,p])

print('Reconstructing with true coefficients')
w = PODrec(at[:,:,1],PHIw[:,:,1])

w = w[:,-1]
w = np.reshape(w,[nx,ny])

fig, ax = plt.subplots(1,1,sharey=True,figsize=(5,4))
cs = ax.contourf(x[:nx],y[:ny],w.T, 120, cmap = 'jet')
ax.set_aspect(1.0)

fig.colorbar(cs,orientation='vertical')
fig.tight_layout() 
plt.show()
fig.savefig("reconstructed.eps", bbox_inches = 'tight')

#%% Galerkin projection [Fully Intrusive]

###############################
# Galerkin projection with nr
###############################
b_l = np.zeros((nr,nr,nc))
b_nl = np.zeros((nr,nr,nr,nc))
linear_phi = np.zeros(((nx)*(ny),nr,nc))
nonlinear_phi = np.zeros(((nx)*(ny),nr,nc))

#%% linear term   
for p in range(nc):
    for i in range(nr):
        phi_w = np.reshape(PHIw[:,i,p],[nx,ny])
        
        lin_term = linear_term(nx,ny,dx,dy,Re[p],phi_w)
        linear_phi[:,i,p] = np.reshape(lin_term,(nx)*(ny))

for p in range(nc):
    for k in range(nr):
        for i in range(nr):
            b_l[i,k,p] = np.dot(linear_phi[:,i,p].T , PHIw[:,k,p]) 
                   
#%% nonlinear term 
for p in range(nc):
    for i in range(nr):
        phi_w = np.reshape(PHIw[:,i,p],[nx,ny])
        for j in range(nr):  
            phi_s = np.reshape(PHIs[:,j,p],[nx,ny])
            nonlin_term = nonlinear_term(nx,ny,dx,dy,phi_w,phi_s)
            jacobian_phi = np.reshape(nonlin_term,(nx)*(ny))
            for k in range(nr):    
                b_nl[i,j,k,p] = np.dot(jacobian_phi.T, PHIw[:,k,p]) 

#%% solving ROM by Adams-Bashforth scheme          
aGP = np.zeros((ns+1,nr,nc))
for p in range(nc):
    aGP[0,:,p] = at[0,:nr,p]
    aGP[1,:,p] = at[1,:nr,p]
    aGP[2,:,p] = at[2,:nr,p]
    for k in range(3,ns+1):
        r1 = rhs(nr, b_l[:,:,p], b_nl[:,:,:,p], aGP[k-1,:,p])
        r2 = rhs(nr, b_l[:,:,p], b_nl[:,:,:,p], aGP[k-2,:,p])
        r3 = rhs(nr, b_l[:,:,p], b_nl[:,:,:,p], aGP[k-3,:,p])
        temp= (23/12) * r1 - (16/12) * r2 + (5/12) * r3
        aGP[k,:,p] = aGP[k-1,:,p] + dt*temp 
        
#%% solving ROM by Adams-Bashforth scheme          
aGPm = np.zeros((ns+1,nr,nc))
for p in range(nc):
    aGPm[0,:,p] = at[0,:nr,p]
    aGPm[1,:,p] = at[1,:nr,p]
    aGPm[2,:,p] = at[2,:nr,p]
    for k in range(3,ns+1):
        r1 = rhs(nr, b_l[:,:,p], b_nl[:,:,:,p], at[k-1,:,p])
        r2 = rhs(nr, b_l[:,:,p], b_nl[:,:,:,p], at[k-2,:,p])
        r3 = rhs(nr, b_l[:,:,p], b_nl[:,:,:,p], at[k-3,:,p])
        temp= (23/12) * r1 - (16/12) * r2 + (5/12) * r3
        aGPm[k,:,p] = at[k-1,:,p] + dt*temp 
        
#%%
def plot_data(t,at,aGP,aGPm):
    fig, ax = plt.subplots(nrows=4,ncols=2,figsize=(12,8))
    ax = ax.flat
    nrs = at.shape[1]
    
    for i in range(nrs):
        ax[i].plot(t,at[:,i],'k',label=r'True Values')
        ax[i].plot(t,aGP[:,i],'b--',label=r'Exact Values')
        ax[i].plot(t,aGPm[:,i],'r-.',label=r'True Values')
        ax[i].set_xlabel(r'$t$',fontsize=14)
        ax[i].set_ylabel(r'$a_{'+str(i+1) +'}(t)$',fontsize=14)    
    
    fig.tight_layout()
    
    fig.subplots_adjust(bottom=0.1)
    line_labels = ["True","Standard GP", "Modified GP"]#, "ML-Train", "ML-Test"]
    plt.figlegend( line_labels,  loc = 'lower center', borderaxespad=0.0, ncol=3, labelspacing=0.)
    plt.show()
    fig.savefig('modes_gp.pdf')

#plot_data(t,at[:,:,-1],aGP[:,:,-1],aGPm[:,:,-1])    

#%% plot basis functions
def plot_data_basis(x,y,PHI):
    fig, ax = plt.subplots(nrows=4,ncols=2,figsize=(10,14))
    ax = ax.flat
    nrs = at.shape[1]
    
    for i in range(nrs):
        f = np.zeros((nx+1,ny+1))
        f[:nx,:ny] = np.reshape(PHI[:,i],[nx,ny])
        f[:,ny] = f[:,1]
        f[nx,:] = f[1,:]
        
        cs = ax[i].contourf(x,y,f.T, 10, cmap = 'jet')
        divider = make_axes_locatable(ax[i])
        cax = divider.append_axes('right', size='5%', pad=0.1)
        fig.colorbar(cs,cax=cax,orientation='vertical')
        ax[i].set_aspect(1.0)
        ax[i].set_title(r'$\phi_{'+str(i+1) + '}$')
        
    fig.tight_layout()    
    plt.show()
    fig.savefig('bases_ns2d.pdf')

#plot_data_basis(x,y,PHIw[:,:,-1])#,aGP[-1,:,:],aGP1[-1,:,:]) 

#%% plot modal coefficients
def plot_data(t,at,aGP,filename):
    fig, ax = plt.subplots(nrows=4,ncols=2,figsize=(12,8))
    ax = ax.flat
    nrs = at.shape[1]
    
    for i in range(nrs):
        #for k in range(at.shape[2]):
        ax[i].plot(t,at[:,i],label=str(k))
        #ax[i].legend(loc=0)
        #ax[i].plot(t,aGP[:,i],label=r'Exact Values')
        #ax[i].plot(t,aGPm[:,i],'r-.',label=r'True Values')
        ax[i].set_xlabel(r'$t$',fontsize=14)
        ax[i].set_ylabel(r'$a_{'+str(i+1) +'}(t)$',fontsize=14)    
    
    fig.tight_layout()
    
    fig.subplots_adjust(bottom=0.1)
    line_labels = ["True","Standard GP","Modified GP"]#, "ML-Train", "ML-Test"]
    plt.figlegend( line_labels,  loc = 'lower center', borderaxespad=0.0, ncol=3, labelspacing=0.)
    plt.show()
    fig.savefig(filename)

#plot_data(t,at[:,:],aGP[:,:],'modes_ns2d.pdf')#,aGP1[-1,:,:])       

#%%
res_proj = at - aGPm # difference betwwen true and modified GP

#%% LSTM using 1 parameter + nr modes as input and nr modes as ouput
# Removing old models
if os.path.isfile('burgers_integrator_LSTM.hd5'):
    os.remove('burgers_integrator_LSTM.hd5')
    
# Stacking data
a = np.zeros((ns+1,nr+1,nc))
for p in range(nc):
    a[:,0,p] = nu[p]*np.ones(ns+1)
    a[:,1:,p] = res_proj[:,:,p]
   

# Create training data for LSTM
lookback = 3 #Number of lookbacks

# use xtrain from here
for p in range(nc):
    xt, yt = create_training_data_lstm(aGPm[:,:,p], ns+1, nr, lookback)
    if p == 0:
        xtrain = xt
        ytrain = yt
    else:
        xtrain = np.vstack((xtrain,xt))
        ytrain = np.vstack((ytrain,yt))

data = xtrain # modified GP as the input data

# use ytrain from here
for p in range(nc):
    xt, yt = create_training_data_lstm(res_proj[:,:,p], ns+1, nr, lookback)
    if p == 0:
        xtrain = xt
        ytrain = yt
    else:
        xtrain = np.vstack((xtrain,xt))
        ytrain = np.vstack((ytrain,yt))

labels = ytrain
        
#%%
# Scaling data
p,q,r = data.shape
data2d = data.reshape(p*q,r)

scalerIn = MinMaxScaler(feature_range=(-1,1))
scalerIn = scalerIn.fit(data2d)
data2d = scalerIn.transform(data2d)
data = data2d.reshape(p,q,r)

scalerOut = MinMaxScaler(feature_range=(-1,1))
scalerOut = scalerOut.fit(labels)
labels = scalerOut.transform(labels)

xtrain, xvalid, ytrain, yvalid = train_test_split(data, labels, test_size=0.2 , shuffle= True)

#%%

m,n = ytrain.shape # m is number of training samples, n is number of output features [i.e., n=nr]

# create the LSTM architecture
model = Sequential()
#model.add(Dropout(0.2))
model.add(LSTM(60, input_shape=(lookback, n), return_sequences=True, activation='tanh'))
#model.add(LSTM(40, input_shape=(lookback, n+1), return_sequences=True, activation='relu', kernel_initializer='uniform'))
model.add(LSTM(60, input_shape=(lookback, n), activation='tanh'))
model.add(Dense(n))

# compile model
#model.compile(loss='mean_absolute_percentage_error', optimizer='adam', metrics=['mean_absolute_percentage_error'])
model.compile(loss='mean_squared_error', optimizer='adam', metrics=['mean_squared_error'])

# run the model
history = model.fit(xtrain, ytrain, epochs=1000, batch_size=32, validation_data= (xvalid,yvalid))

# evaluate the model
scores = model.evaluate(xtrain, ytrain, verbose=0)
print("%s: %.2f%%" % (model.metrics_names[1], scores[1]*100))

loss = history.history['loss']
val_loss = history.history['val_loss']

plt.figure()
epochs = range(1, len(loss) + 1)
plt.semilogy(epochs, loss, 'b', label='Training loss')
plt.semilogy(epochs, val_loss, 'r', label='Validationloss')
plt.title('Training and validation loss')
plt.legend()
filename = 'loss.png'
plt.savefig(filename, dpi = 400)
plt.show()

# Save the model
filename = 'burgers_integrator_LSTM.hd5'
model.save(filename)

#%% Testing
# Data generation for testing

uTest = np.zeros((nx*ny, ns+1))
upTest = np.zeros((nx*ny, ns+1))
uoTest = np.zeros((nx*ny, ns+1))
nuTest = 1/ReTest

for n in range(ns+1):
   file_input = "./snapshots/Re_"+str(int(ReTest))+"/w/w_"+str(int(n))+ ".csv"
   w = np.genfromtxt(file_input, delimiter=',')
    
   w1 = w[1:nx+1,1:ny+1]
    
   uTest[:,n] = np.reshape(w1,(nx)*(ny)) #snapshots from unperturbed solution
   upTest[:,n] = noise*uTest[:,n] #perturbation (unknown physics)
   uoTest[:,n] = uTest[:,n] + upTest[:,n] #snapshots from observed solution

w_fom = uoTest[:,-1] # last time step
w_fom = np.reshape(w_fom,[nx,ny])
w_fom = pbc(w_fom)

#% POD basis computation     
print('Computing testing POD basis...')
PHItrue, Ltrue, RICtrue  = POD(uoTest, nr) 
        
#% Calculating true POD coefficients
print('Computing testing POD coefficients...')
aTrue = PODproj(uoTest,PHItrue)

#%% Basis Interpolation
pref = 2 #Reference case in [0:nRe]
PHIwtest = GrassInt(PHIw,pref,nu,nuTest)
aTest = PODproj(uoTest,PHIwtest)

print('Reconstructing with true coefficients for test Re')
w_test = PODrec(aTest[:,:],PHIwtest[:,:])

w_test = w_test[:,-1]
w_test = np.reshape(w_test,[nx,ny])
w_test = pbc(w_test)

PHIstest = np.zeros(((nx)*(ny),nr))

for i in range(nr):
    phi_w = np.reshape(PHIwtest[:,i],[nx,ny])
    phi_s = fpsi(nx, ny, dx, dy, -phi_w)    
    PHIstest[:,i] = np.reshape(phi_s,(nx)*(ny))

#%%
b_l = np.zeros((nr,nr))
b_nl = np.zeros((nr,nr,nr))
linear_phi = np.zeros(((nx)*(ny),nr))
nonlinear_phi = np.zeros(((nx)*(ny),nr))
 
for k in range(nr):
    phi_w = np.reshape(PHIwtest[:,k],[nx,ny])
    lin_term = linear_term(nx,ny,dx,dy,ReTest,phi_w)
    linear_phi[:,k] = np.reshape(lin_term,(nx)*(ny))

for k in range(nr):
    for i in range(nr):
        b_l[i,k] = np.dot(linear_phi[:,i].T , PHIwtest[:,k]) 
                   
for i in range(nr):
    phi_w = np.reshape(PHIwtest[:,i],[nx,ny])
    for j in range(nr):  
        phi_s = np.reshape(PHIstest[:,j],[nx,ny])
        nonlin_term = nonlinear_term(nx,ny,dx,dy,phi_w,phi_s)
        jacobian_phi = np.reshape(nonlin_term,(nx)*(ny))
        for k in range(nr):    
            b_nl[i,j,k] = np.dot(jacobian_phi.T, PHIwtest[:,k]) 
       
aGPtest = np.zeros((ns+1,nr))
aGPtest[0,:] = aTest[0,:nr]
aGPtest[1,:] = aTest[1,:nr]
aGPtest[2,:] = aTest[2,:nr]
for k in range(3,ns+1):
    r1 = rhs(nr, b_l[:,:], b_nl[:,:,:], aGPtest[k-1,:])
    r2 = rhs(nr, b_l[:,:], b_nl[:,:,:], aGPtest[k-2,:])
    r3 = rhs(nr, b_l[:,:], b_nl[:,:,:], aGPtest[k-3,:])
    temp= (23/12) * r1 - (16/12) * r2 + (5/12) * r3
    aGPtest[k,:] = aGPtest[k-1,:] + dt*temp 

print('Reconstructing with GP coefficients for test Re')
w_gp = PODrec(aGPtest[:,:],PHIwtest[:,:])

w_gp = w_gp[:,-1]
w_gp = np.reshape(w_gp,[nx,ny])
w_gp = pbc(w_gp)

#%%
#aGPmtest = np.zeros((ns+1,nr))
#aGPmtest[0,:] = aTest[0,:nr]
#aGPmtest[1,:] = aTest[1,:nr]
#aGPmtest[2,:] = aTest[2,:nr]
#for k in range(3,ns+1):
#    r1 = rhs(nr, b_l[:,:], b_nl[:,:,:], aTest[k-1,:])
#    r2 = rhs(nr, b_l[:,:], b_nl[:,:,:], aTest[k-2,:])
#    r3 = rhs(nr, b_l[:,:], b_nl[:,:,:], aTest[k-3,:])
#    temp= (23/12) * r1 - (16/12) * r2 + (5/12) * r3
#    aGPmtest[k,:] = aTest[k-1,:] + dt*temp 
        
#plot_data(t,aTest[:,:],aGPtest[:,:],aGPmtest[:,:],'modes_test.pdf')

#%% LSTM [Fully Nonintrusive]
# testing
testing_set = aTest
m,n = testing_set.shape
xtest = np.zeros((1,lookback,nr))
rLSTM = np.zeros((ns+1,nr))
aGPmlc = np.zeros((ns+1,nr))

#%%
# Initializing
for i in range(lookback):
    temp = testing_set[i,:]
    temp = temp.reshape(1,-1)
    xtest[0,i,:]  = temp
    rLSTM[i, :] = testing_set[i,:] - aGPtest[i,:] 
    aGPmlc[i,:] = testing_set[i,:] # modified GP + correction = True
    

#%%
# Prediction
'''
iterative prediction with correction = True - modified GP
'''
for i in range(lookback,ns+1):
    xtest_sc = scalerIn.transform(xtest[0])
    xtest_sc = xtest_sc.reshape(1,lookback,nr)
    ytest_sc = model.predict(xtest_sc)
    ytest = scalerOut.inverse_transform(ytest_sc) # residual/ correction
    rLSTM[i, :] = ytest
        
    for k in range(lookback-1):
        xtest[0,k,:] = xtest[0,k+1,:]
    
    r1 = rhs(nr, b_l[:,:], b_nl[:,:,:], aGPmlc[i-1,:])
    r2 = rhs(nr, b_l[:,:], b_nl[:,:,:], aGPmlc[i-2,:])
    r3 = rhs(nr, b_l[:,:], b_nl[:,:,:], aGPmlc[i-3,:])
    temp= (23/12) * r1 - (16/12) * r2 + (5/12) * r3
    
    aGPmlc[i,:] = aGPmlc[i-1,:] + dt*temp + ytest
            
    xtest[0,lookback-1,:] = aGPmlc[i,:] 

print('Reconstructing with true coefficients for test Re')
w_ml = PODrec(aGPmlc[:,:],PHIwtest[:,:])

w_ml = w_ml[:,-1]
w_ml = np.reshape(w_ml,[nx,ny])
w_ml = pbc(w_ml)

#%%
modal_coeffs = np.hstack((aTest,aGPtest,aGPmlc))
field = np.zeros((4,w_fom.shape[0],w_fom.shape[1]))
field[0] = w_fom
field[1] = w_test
field[2] = w_gp
field[3] = w_ml

filename = "./plotting/h1_modes_"+str(int(ReTest))+".csv"
np.savetxt(filename, modal_coeffs, delimiter=",")
    
with open("./plotting/h1_field_"+str(int(ReTest))+".csv", 'w') as outfile:
    outfile.write('# Array shape: {0}\n'.format(field.shape))
    for data_slice in field:
        np.savetxt(outfile, data_slice, delimiter=",")
        outfile.write('# New slice\n')
                      
#%%
def plot_data_allmodes(t,at,aTest,filename):
    fig, ax = plt.subplots(nrows=4,ncols=2,figsize=(12,8))
    ax = ax.flat
    nrs = at.shape[1]
    
    for i in range(nrs):
        #for k in range(at.shape[2]):
        ax[i].plot(t,at[:,i],label=str(k))
        ax[i].plot(t,aTest[:,i],'k--',label=str(k))
        #ax[i].legend(loc=0)
        #ax[i].plot(t,aGP[:,i],label=r'Exact Values')
        #ax[i].plot(t,aGPm[:,i],'r-.',label=r'True Values')
        ax[i].set_xlabel(r'$t$',fontsize=14)
        ax[i].set_ylabel(r'$a_{'+str(i+1) +'}(t)$',fontsize=14)    
    
    fig.tight_layout()
    
    fig.subplots_adjust(bottom=0.1)
    line_labels = ["Re=200","Re=400","Re=600","Re=800","Test"]#, "ML-Train", "ML-Test"]
    plt.figlegend( line_labels,  loc = 'lower center', borderaxespad=0.0, ncol=5, labelspacing=0.)
    plt.show()
    fig.savefig(filename)

#plot_data_allmodes(t,at[:,:],aTest,'modes_all.pdf')#,aGP1[-1,:,:])
