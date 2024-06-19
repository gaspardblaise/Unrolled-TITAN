"""
UTitan model classes
Classes
-------
    ISI_loss  : defines the ISI training loss 
    nn_alpha    : predicts the regularisation parameter
    W_iter     : computes the updates of W
    C_iter    : computes the updates of C
    Block      : one layer in U_TITAN
    myModel    : U_TITAN model


@author: Gaspard Blaise
@date: 11/06/2024
"""


import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from functions import *
from tools import *


class ISI_loss():
    """
    Defines the ISI training loss.
    Attributes
    ----------
        ISI : function computing the ISI Score 
    """
    def __init__(self): 
        super(ISI_loss, self).__init__()
        
 
    def __call__(self, input, target):
        """
        Computes the training loss.
        Parameters
        ----------
            input  (torch.FloatTensor): restored images, size n*c*h*w 
            target (torch.FloatTensor): ground-truth images, size n*c*h*w
        Returns
        -------
            (torch.FloatTensor): ISI Score, size 1 
        """
        return joint_isi(input, target)



class FCNN_alpha(nn.Module):
    """
    Predicts the regularization parameter alpha given W and C.
    Attributes
    ----------
        fc1, fc2, fc3 (torch.nn.Linear): fully connected layers
        soft          (torch.nn.Softplus): Softplus activation function
    """
    def __init__(self, input_size, hidden_size1, hidden_size2, output_size=1):
        super(FCNN_alpha, self).__init__()
        self.fc1 = nn.Linear(input_size, hidden_size1)
        self.fc2 = nn.Linear(hidden_size1, hidden_size2)
        self.fc3 = nn.Linear(hidden_size2, output_size)
        self.soft = nn.Softplus()
        
    def forward(self, W, C):
        """
        Computes the regularization parameter alpha.
        Parameters
        ----------
            W (torch.FloatTensor): input tensor W, size [batch_size, W_size]
            C (torch.FloatTensor): input tensor C, size [batch_size, C_size]
        Returns
        -------
            torch.FloatTensor: alpha parameter, size [batch_size, 1]
        """
        # Flatten W to size [N*N*K]
        W = W.view(-1)
        # Flatten C to size [K*K*N]
        C = C.view(-1)
        # Concatenate W and C along the last dimension
        x = torch.cat((W, C))
        x = self.soft(self.fc1(x))
        x = self.soft(self.fc2(x))
        x = self.fc3(x)
        x = self.soft(x)
        return x






class W_iter(nn.Module):

    def __init__(self,N_updates_W):
        super(W_iter, self).__init__()
        self.N_updates_W = N_updates_W

    def inertial_step(self,beta_w,W,W_old):
        W = W + beta_w * (W - W_old)
        return W
    
    def gradient_step(self,Rx,c_w,W,C):
        W = W - c_w * grad_H_W(W, C, Rx)
        return W

    def prox_step(self,c_w,W):
        return prox_f(W, c_w)

    def update(self,Rx,W,W_old,C,c_w,beta_w):
        W_inertial = self.inertial_step(beta_w,W,W_old)
        W_old = W.clone()
        W_gradient = self.gradient_step(Rx,c_w,W_inertial,C)
        W_prox = self.prox_step(c_w,W_gradient)
        return W_prox,W_old
    
    def forward(self,Rx,W,C,c_w,beta_w):
        W_old = W.clone()
        for _ in range(self.N_updates_W):
            W,W_old = self.update(Rx,W,W_old,C,c_w,beta_w)
        return W
    


class C_iter(nn.Module):
    def __init__(self,N_updates_C):
        super(C_iter, self).__init__()
        self.N_updates_C = N_updates_C

    def inertial_step(self,beta_c,C,C_old):
        C = C + beta_c * (C - C_old)
        return C
    
    def gradient_step(self,Rx,c_c,C,W,alpha):
        C = C - c_c * grad_H_C_reg(W, C, Rx, alpha)
        return C

    def prox_step(self,c_c,C,eps):
        return prox_g(C, c_c, eps)
    
    def update(self,Rx,C,C_old,W_updated,c_c,beta_c,alpha,eps):
        C_inertial = self.inertial_step(beta_c,C,C_old)
        C_old = C.clone()
        C_gradient = self.gradient_step(Rx,c_c,C_inertial,W_updated,alpha)
        C_prox = self.prox_step(c_c,C_gradient,eps)
        return C_prox,C_old

    def forward(self,Rx,C,W_updated,c_c,beta_c,alpha,eps):
        for _ in range(self.N_updates_C):
            C_old = C.clone()
            C,C_old = self.update(Rx,C,C_old,W_updated,c_c,beta_c,alpha,eps)
            return C,C_old




class Block(nn.Module):

    """
    One layer in U_TITAN.
    Attributes
    ----------
        nn_bar                           (Cnn_bar): computes the barrier parameter
        soft                    (torch.nn.Softplus): Softplus activation function
        gamma                (torch.nn.FloatTensor): stepsize, size 1 
        reg_mul,reg_constant (torch.nn.FloatTensor): parameters for estimating the regularization parameter, size 1
        delta                               (float): total variation smoothing parameter
        IPIter                             (IPIter): computes the next proximal interior point iterate
    """


    def __init__(self,input_dim, N_updates_W,N_updates_C,gamma_c,gamma_w,eps,nu,zeta):
    
        super(Block, self).__init__()
        self.input_dim = input_dim
        self.NN_alpha = FCNN_alpha(input_dim, 64, 32)
        #self.soft         = nn.Softplus()
        #self.reg_mul      = nn.Parameter(torch.FloatTensor([-7]).cuda()) 
        #self.reg_constant = nn.Parameter(torch.FloatTensor([-5]).cuda())
        self.W_iter = W_iter(N_updates_W)
        self.C_iter = C_iter(N_updates_C)
        self.gamma_c = gamma_c
        self.gamma_w = gamma_w
        self.eps = eps
        self.nu = nu
        self.zeta = zeta
    
    def get_coefficients(self,Rx,alpha,C,C_old):
        
        K,_,N = C.shape
        #print("alpha",alpha)

        rho_Rx = spectral_norm_extracted(Rx,K,N)
        #print("rho_Rx",rho_Rx)
        l_sup = torch.max((self.gamma_w*alpha)/(1-self.gamma_w),rho_Rx*2*K*(1+torch.sqrt(2/(alpha*self.gamma_c))))

        expr3 = torch.tensor(self.gamma_c**2 / K**2, dtype=torch.float32)
        expr4 = alpha * self.gamma_w / ((1 + self.zeta) * (1 - self.gamma_w) * l_sup)
        expr5 = rho_Rx / ((1 + self.zeta) * l_sup)

        # Compute the minimum of the three expressions
        C0 = torch.min(torch.min(expr3, expr4), expr5)


        L_inf = (1+self.zeta)*C0*l_sup
        L_w_prev = torch.max(L_inf,lipschitz(C_old,rho_Rx))
        L_w = torch.max(L_inf,lipschitz(C,rho_Rx))

        c_w = self.gamma_w/L_w
        beta_w = (1-self.gamma_w)*torch.sqrt(C0*self.nu*(1-self.nu)*L_w_prev/L_w)

        c_c = self.gamma_c/alpha
        beta_c = torch.sqrt(C0 * self.nu*(1-self.nu))

        return c_w,beta_w,c_c,beta_c

    def forward(self,Rx,W,C,C_old):
        """
        Computes the next iterate, output of the layer.
        Parameters
        ----------
      	    x            (torch.nn.FloatTensor): previous iterate, size n*c*h*w
            Ht_x_blurred (torch.nn.FloatTensor): Ht*degraded image, size n*c*h*w
            std_approx   (torch.nn.FloatTensor): approximate noise standard deviation, size n*1
            save_gamma_mu_lambda          (str): indicates if the user wants to save the values of the estimated hyperparameters, 
                                                 path to the folder to save the hyperparameters values or 'no' 
        Returns
        -------
       	    (torch.FloatTensor): next iterate, output of the layer, n*c*h*w
        """


        alpha = self.NN_alpha(W,C)
        c_w,beta_w,c_c,beta_c = self.get_coefficients(Rx,alpha,C,C_old)

        W_updated = self.W_iter(Rx,W,C,c_w,beta_w)
        C_updated,C_old = self.C_iter(Rx,C,W_updated,c_c,beta_c,alpha,self.eps)
        return W_updated,C_updated,C_old




class myModel(nn.Module):
    """
    U_TITAN model.
    Attributes
    ----------
        blocks (list): list of Blocks
    """
    def __init__(self,input_dim, N_updates_W,N_updates_C,num_layers,gamma_c,gamma_w,eps,nu,zeta):
        super(myModel, self).__init__()
        self.Layers = nn.ModuleList([Block(input_dim,N_updates_W,N_updates_C,gamma_c,gamma_w,eps,nu,zeta) for _ in range(num_layers)])



    def GradFalse(self,block,mode):
        """
        Initializes current layer's parameters with previous layer's parameters, fixes the parameters of the previous layers.
        Parameters
        ----------
      	    block (int): block-1 is the layer to be trained
            mode  (str): 'greedy' if training one layer at a time, 'last_layers_lpp' if training the last 10 layers + lpp
        """
        if block>=1:
            if mode=='greedy':
                self.Layers[block].load_state_dict(self.Layers[block-1].state_dict())
            for i in range(0,block):
                self.Layers[i].eval()
                for p in self.Layers[i].parameters():
                    p.requires_grad = False
                    p.grad          = None


    def forward(self,Rx,W,C,mode,layer=0):
        """
        Computes the next iterate, output of the model.
        Parameters
        ----------
      	    W,C            (torch.nn.FloatTensor): previous iterates, size n*N*N*K, n*K*K*N
            Rx             (torch.nn.FloatTensor): covariance matrix of the input, size K*K*N
            mode          (str): 'first_layer' if training the first layer, 'greedy' if training one layer at a time, 'test' if testing
            layer                         (int): layer-1 is the layer to be trained (default is 0)
            save_gamma_mu_lambda          (str): indicates if the user wants to save the values of the estimated hyperparameters, 
                                                 path to the folder to save the hyperparameters values or 'no' 
        Returns
        -------
       	    (torch.FloatTensor): next iterates, output of the model, n*N*N*K, n*K*K*N
        """
        if mode=='first_layer' or mode=='greedy':
            C_old = C.clone()
            W,C,C_old = self.Layers[layer](Rx,W,C,C_old)
        elif mode=='test':
            for i in range(len(self.Layers)):
                # we use .detach() to avoid computing and storing the gradients since the model is being tested
                C_old = C.clone()
                W,C,C_old = self.Layers[i](Rx,W,C,C_old)

        return W,C
    

















"""




class DeterministicBlock(nn.Module):
    def __init__(self, Rx, rho_Rx, gamma_c, gamma_w, eps, nu, zeta):
        super(DeterministicBlock, self).__init__()
        self.Rx = Rx
        self.rho_Rx = rho_Rx
        self.gamma_c = gamma_c
        self.gamma_w = gamma_w
        self.eps = eps 
        self.nu = nu
        self.zeta = zeta
        self.device = 'cuda:0'    

    def forward(self, W, C, alpha, L_w_prev):
        K = W.shape[2]
        alpha = alpha.to(self.device)
        #print(alpha.device)

        l_sup = max((self.gamma_w*alpha)/(1-self.gamma_w),self.rho_Rx*2*K*(1+torch.sqrt(2/(alpha*self.gamma_c))))
        C0 = min(self.gamma_c**2/K**2,(alpha*self.gamma_w/((1+self.zeta)*(1 - self.gamma_w)*l_sup)),(self.rho_Rx/((1+self.zeta)*l_sup)))
        l_inf = (1+self.zeta)*C0*l_sup

        c_c = self.gamma_c / alpha
        beta_c = torch.sqrt(C0*self.nu*(1-self.nu))
        L_w = max(l_inf,lipschitz(C,self.rho_Rx))
        c_w = self.gamma_w / L_w
        beta_w = (1 - self.gamma_w) * torch.sqrt(C0 * self.nu * (1 - self.nu) * L_w_prev / L_w)
        W_prev = W.clone()
        W_prev = W_prev.to(self.device)
        #print(W.device)	

        for _ in range(10):
            
            W_tilde = W + beta_w * (W - W_prev)
            grad_W = grad_H_W(W_tilde, C, self.Rx)
            W_bar = W_tilde - c_w * grad_W
            W_prev = W.clone()
            W = prox_f(W_bar, c_w)

        C_prev = C.clone()
        beta_c = torch.sqrt(C0 * self.nu * (1 - self.nu))
        C_tilde = C + beta_c * (C - C_prev)
        grad_C = grad_H_C_reg(W, C_tilde, self.Rx, alpha)
        C_bar = C_tilde - c_c * grad_C
        C_prev = C.clone()
        C = prox_g(C_bar, c_c, self.eps)

        return W, C, L_w




# Define the TitanLayer
class TitanLayer(nn.Module):
    def __init__(self, Rx, rho_Rx, gamma_c, gamma_w, eps, nu, input_dim, zeta):
        super(TitanLayer, self).__init__()
        self.alpha_net = AlphaNetwork(input_dim)
        self.deterministic_block = DeterministicBlock(Rx, rho_Rx, gamma_c, gamma_w, eps, nu, zeta)

    def forward(self, W, C, L_w_prev):
        alpha = self.alpha_net(W, C)
        W, C, L_w = self.deterministic_block(W, C, alpha, L_w_prev)
        return W, C, L_w, alpha
    




class TitanIVAGNet(nn.Module):
    def __init__(self, input_dim, num_layers=20, gamma_c=1, gamma_w=0.99, eps=1e-12, nu=0.5, zeta=0.1):
        super(TitanIVAGNet, self).__init__()
        self.num_layers = num_layers
        self.gamma_c = torch.tensor(gamma_c)
        self.gamma_w = torch.tensor(gamma_w)
        self.eps = torch.tensor(eps)
        self.nu = torch.tensor(nu)  
        self.zeta = torch.tensor(zeta)
        self.alpha_network = AlphaNetwork(input_dim)
        self.alphas = [torch.FloatTensor([1]).to('cuda') for _ in range(num_layers)]
        self.input_dim = input_dim
        self.layers = nn.ModuleList([
            TitanLayer(None, None, gamma_c, gamma_w, eps, nu, input_dim, zeta)
            for _ in range(num_layers)
        ])
    


    def initialize_L_w(self, C, rho_Rx, K):
        l_sup = max((self.gamma_w * self.alphas[0]) / (1 - self.gamma_w), rho_Rx * 2 * K * (1 + torch.sqrt(2 / (self.alphas[0] * self.gamma_c))))
        C0 = min(self.gamma_c**2 / K**2, (self.alphas[0] * self.gamma_w / ((1 + self.zeta) * (1 - self.gamma_w) * l_sup)), (rho_Rx / ((1 + self.zeta) * l_sup)))
        l_inf = (1 + self.zeta) * C0 * l_sup
        return max(l_inf, lipschitz(C, rho_Rx))
    
    
    def forward(self, X, A):
        N,_,K = X.shape
        input_dim = N * N * K + K * K * N
        Rx = cov_X(X)
        rho_Rx = spectral_norm_extracted(Rx, K, N)

        
        W, C = initialize(N, K, init_method='random', Winit=None, Cinit=None, X=X, Rx=Rx, seed=None)
        
        L_w_prev = self.initialize_L_w(C, rho_Rx, K)


        for i, layer in enumerate(self.layers):

            layer.deterministic_block = DeterministicBlock(Rx, rho_Rx, self.gamma_c, self.gamma_w, self.eps, self.nu, self.zeta)  # Ensure each layer has its own deterministic block
            W, C, L_w, alpha = layer(W, C, L_w_prev)
            L_w_prev = L_w
            self.alphas[i] = alpha
            

        
        isi_score = joint_isi(W, A)

        return W, C, isi_score

"""