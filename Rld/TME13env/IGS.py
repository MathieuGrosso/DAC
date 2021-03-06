import argparse
import sys
import matplotlib
#matplotlib.use("Qt5agg")
matplotlib.use("TkAgg")
import gym
import gridworld
import torch
from utils import *
from torch.utils.tensorboard import SummaryWriter
from torch import nn
import torch
from random import random
from pathlib import Path
from memory import Memory
import numpy as np
from collections import deque, defaultdict
from torch.distributions import Categorical

import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'

device = 'cuda' if torch.cuda.is_available() else 'cpu'

class Qfunc(nn.Module):
    def __init__(self, size_in, size_out, layers=[], activation=nn.ReLU()):
        super(Qfunc,self).__init__()
        self.module = nn.ModuleList([])   
        for x in layers:
            self.module.append(nn.Linear(size_in, x))
            self.module.append(activation)
            size_in = x
        self.module.append(nn.Linear(size_in, size_out))
    
    def setcuda(self, device):
        self.cuda(device=device)

    def forward(self, x, g):
        x = torch.cat((x,g), dim=-1)
        for l in self.module:
            x = l(x)
        return x

class DQNAgent(object):

    def __init__(self, env, opt, H=[30], lr=1e-1, gamma=0.95,
        eps0=1., nu=1e-1, freq_update_target=100):
        self.opt=opt
        self.env=env
        if opt.fromFile is not None:
            self.load(opt.fromFile)
    
        # initialize
        self.action_space = env.action_space
        self.featureExtractor = opt.featExtractor(env)

        self.eps = lambda t : eps0
        self.epoch , self.iteration = 0, 0
        self.freq_update_target = freq_update_target

        #create q functions and optimizers
        self.q = Qfunc(size_in=2*self.featureExtractor.outSize, 
            size_out=self.action_space.n,
            layers=H,
            activation=nn.Hardtanh())
        self.q_hat = Qfunc(size_in=2*self.featureExtractor.outSize, 
            size_out=self.action_space.n,
            layers=H,
            activation=nn.Hardtanh())
        self.q_hat.load_state_dict(self.q.state_dict())
        self.q.to(dtype=float, device=device)  
        self.q_hat.to(dtype=float, device=device)

        self.optim = torch.optim.Adam(
            params=self.q.parameters(), lr=lr)

        self.criterion = torch.nn.MSELoss().to(dtype=float)



    def act(self, observation, reward, done, t, goal):
        if random()<self.eps(t):
            return self.action_space.sample()
        with torch.no_grad(): 
            s = torch.tensor(self.featureExtractor.getFeatures(observation), device=device, dtype=float)
            g = torch.tensor(goal, device=device, dtype=float)
            q = self.q(s,g)
            a = int(np.argmax(q))
            return a

    def train(self, batch):
        n = len(batch)
        phi, actions, phi_new, reward, done, goal = [], [], [], [], [], []
        for e in batch:
            phi.append(e[0])
            actions.append(e[1])
            phi_new.append(e[2])
            reward.append(e[3])
            done.append(e[4])
            goal.append(e[5])
        s = torch.tensor(phi, device=device, dtype=float).view(n,-1)
        a = torch.tensor(actions, device=device, dtype=int)
        s_p = torch.tensor(phi_new, device=device, dtype=float).view(n,-1)
        r = torch.tensor(reward, device=device, dtype=float)
        done = torch.tensor(done, device=device, dtype=float)
        g = torch.tensor(goal, device=device, dtype=float).view(n,-1)

        mean_r = r.clamp(0.).mean()

        with torch.no_grad():
            y = r + gamma * agent.q_hat(s_p, g).max(dim=1).values
        indices = torch.arange(n, device=device)
        q = self.q(s, g)[indices, a]
        loss = self.criterion(y.detach(), q)

        self.optim.zero_grad()
        loss.backward()
        self.optim.step()
        self.iteration += 1
        
        return loss, mean_r

    def update_target(self):
        self.q_hat.load_state_dict(self.q.state_dict())

def sample_goal(G, N, V, alpha):

    n = torch.tensor([ N[g] for g in G ], dtype=float)
    v = torch.tensor([ V[g] for g in G ], dtype=float)
    n = n.clamp(min=1.)
    x = v / n
    H = - x * torch.log(x) - (1 - x) * torch.log(1 - x)
    logits = torch.exp(alpha * H)

    logits = Categorical(logits=logits)
    idx = int(logits.sample().item())
    return G[idx]

def goal_in_G(G, goal):
    for g in G:
        if (g==goal).all():
            return True
    return False


if __name__ == '__main__':
    config = load_yaml('./configs/config_random_gridworld_IGS.yaml')
    freqTest = config.freqTest
    freqSave = config.freqSave
    nbTest = config.nbTest
    env = gym.make(config.env)
    if hasattr(env, 'setPlan'):
        env.setPlan(config.map, config.rewards)
    tstart = str(time.time())
    tstart = tstart.replace(".", "_")
    env.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    episode_count = config.nbEpisodes
    ob = env.reset()

    featureExtractor = config.featExtractor(env)
    print("fe", featureExtractor.outSize)

    H = [200,200]
    lr = 1e-3
    gamma = 0.99
    eps0 = 0.2
    nu = 1e-1
    freq_update_target = 1000
    mem_size = 1000000
    mini_batch = 1000
    mini_batch_pex = 100
    freqOptim = 10
    PEX = False
    beta = 0.5
    alpha = 0.1
    HER = False

    #---agent---#
    agent_id = f'ISG_h{H}_lr{lr}_g{gamma}_eps0{eps0}_nu{nu}_clear{freq_update_target}_freqOptim{freqOptim}_memsize{mem_size}'
    agent_dir = f'models/{config["env"]}/'
    os.makedirs(agent_dir, exist_ok=True)
    savepath = Path(f'{agent_dir}{agent_id}.pch')
    agent = DQNAgent(env, config, H=H, lr=lr, gamma=gamma,
        eps0=eps0, nu=nu, freq_update_target=freq_update_target)
    # agent.load(savepath)                        # the agent already exists
        

    outdir = "./XP/" + config.env + "/dqn_" + "-" + agent_id + "-" + tstart
    print("Saving in " + outdir)
    os.makedirs(outdir, exist_ok=True)
    save_src(os.path.abspath(outdir))
    write_yaml(os.path.join(outdir, 'info.yaml'), config)
    logger = LogMe(SummaryWriter(outdir))
    replay = Memory(mem_size=mem_size, prior=False)
    G = deque(maxlen=10)
    N = defaultdict(lambda: 0) 
    V = defaultdict(lambda: 0) 

    rsum = 0
    mean = 0
    verbose = True
    itest = 0
    reward = 0
    done = False
    it = 0
    for i in range(episode_count):
        if i % int(config.freqVerbose) == 0 and i >= config.freqVerbose:
            verbose = False # True
        else:
            verbose = False

        if i % freqTest == 0 and i >= freqTest:  
            mean = 0
            agent.test = True

        if i % freqTest == nbTest and i > freqTest:
            print("End of test, mean reward=", mean / nbTest)
            itest += 1
            logger.direct_write("rewardTest", mean / nbTest, itest)
            agent.test = False

        if i % freqSave == 0:
            pass

        j, k = 0, 0
        if verbose:
            env.render()

        loss, mean_r = 0, 0
        goal = None
        phi = featureExtractor.getFeatures(ob)
        temp_replay = []

        while j < 100:
            if verbose:
                env.render()

            if goal is None:
                isg = random() < beta and len(G) > 0
                if  isg:
                    goal = sample_goal(G, N, V, alpha)
                else:
                    goal, _ = env.sampleGoal()
                    goal = featureExtractor.getFeatures(goal)

            action = agent.act(ob, reward, done, i, goal)
         
            ob_new, _, _, _ = env.step(action)
            phi_new = featureExtractor.getFeatures(ob_new)
            done = (phi_new==goal).all()
            reward = 1. if done else -0.1
            temp_replay.append((phi, action, phi_new))
            
            ob = ob_new
            phi = phi_new
            it += 1
            j+=1
            rsum += reward
            if it % freqOptim == 0 and replay.nentities > mini_batch:
                batch = replay.sample(n=mini_batch)
                loss_train, mean_r_train = agent.train(batch=batch)
                loss += loss_train
                mean_r += mean_r_train
                k += 1
            if it % freq_update_target ==0:
                agent.update_target()

            if done or j == 100:
                goal_HER = temp_replay[-1][2] # the last state
                for s, a, s_p in temp_replay:
                    d = (s_p==goal).all()
                    r = 1. if d else -0.1
                    replay.store((s, a, s_p, r, d, goal))
                    if HER:
                        d_HER = (s_p==goal_HER).all()
                        r_HER = 1. if d_HER else -0.1
                        replay.store((s, a, s_p, r_HER, d_HER, goal_HER))
                if isg:
                    N[goal] += 1
                    if done:
                        V[goal] += 1
                else:
                    if i % 10 == 0:
                        print(str(i) + " rsum=" + str(rsum) + ", " + str(j) + " actions ")
                    logger.direct_write("reward/isg", rsum, i)
                    rsum = sum(1 if (e[2]==goal).all() else -0.1 for e in temp_replay)
                    logger.direct_write("reward/goal", rsum, i)
                    logger.direct_write("train/loss", loss/max(k,1), i)
                    logger.direct_write("train/mean", mean_r/max(k,1), i)
                    x, y = phi[0][0], phi[0][1]
                    logger.direct_write("finalposition/x", x, i)
                    logger.direct_write("finalposition/y", y, i)
                    agent.nbEvents = 0
                    mean += rsum
                    rsum = 0
                    ob = env.reset()
                    break
                goal = None

        if goal_in_G(G, phi):
            G.append(phi)
                
        agent.epoch += 1
    env.close()