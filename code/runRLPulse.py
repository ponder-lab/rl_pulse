#!/usr/bin/env python
#
# python runRLPulse.py learningRate numExp bufferSize batchSize ...
#                 polyak updateEvery actorLSTM actorHidden ...
#                 criticLSTM criticHidden
#

print("starting runRLPulse script...")

import sys
import os
import rlPulse as rlp
import spinSimulation as ss
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

print("imported libraries...")

# define prefix for output files

prefix = "dat-" + "-".join(sys.argv[1:]) + "-" + \
            datetime.now().strftime("%Y%m%d-%H%M%S")

hyperparameters = ["Learning rate", "Num experiences", "Buffer size", \
    "Batch size", "Polyak", "Update every", "Actor LSTM layers", \
    "Actor hidden layers", "Critic LSTM layers", "Critic hidden layers"]

# make a new data directory if it doesn't exist
os.mkdir("../data/" + prefix)
output = open("../data/" + prefix + "/output.txt", "a")
output.write("Output file for run\n\n")
output.write("\n".join([i+": "+j for i,j in zip(hyperparameters, sys.argv[1:])]))
output.write("\n" + "="*20 + "\n\n")
output.flush()
print(f"created data directory, data files stored in {prefix}")

# initialize system parameters

N = 4
dim = 2**N

# pulse = .25e-6    # duration of pulse
# delay = 3e-6      # duration of delay
coupling = 5e3    # coupling strength
delta = 500       # chemical shift strength (for identical spins)

(x,y,z) = (ss.x, ss.y, ss.z)
(X,Y,Z) = ss.getTotalSpin(N, dim)

Hdip, Hint = ss.getAllH(N, dim, coupling, delta)
HWHH0 = ss.getHWHH0(X,Y,Z,delta)

print("initialized system parameters")

# initialize RL algorithm hyperparameters

sDim = 3 # state represented by sequences of actions...
aDim = 3 # action = [phi, rot, time]
learningRate = float(sys.argv[1]) # learning rate for optimizer

numExp = int(sys.argv[2]) # how many experiences to "play" through
bufferSize = int(sys.argv[3]) # size of the replay buffer (i.e.
                              # how many experiences to keep in memory).
batchSize = int(sys.argv[4]) # size of batch for training
polyak = float(sys.argv[5]) # polyak averaging parameter
gamma = .5 # future reward discount rate

updateAfter = 1000 # start updating actor/critic networks after this many episodes
updateEvery = int(sys.argv[6])  # update networks every __ episodes
if updateEvery > bufferSize:
    print("updateEvery is larger than bufferSize, reconsider this...")
    raise
numUpdates = 1 # how many training updates to perform on a random subset of
               # experiences (s,a,r,s1,d)
testEvery = 1000

p = .5 # action noise parameter
dp = -p/numExp / (3/4)

# define actors/critics

actor = rlp.Actor(sDim,aDim, learningRate, int(sys.argv[7]), int(sys.argv[8]))
actorTarget = rlp.Actor(sDim,aDim, learningRate, int(sys.argv[7]), int(sys.argv[8]))
critic = rlp.Critic(sDim, aDim, gamma, learningRate, int(sys.argv[9]), int(sys.argv[10]))
criticTarget = rlp.Critic(sDim, aDim, gamma, learningRate, int(sys.argv[9]), int(sys.argv[10]))
env = rlp.Environment(N, dim, sDim, HWHH0, X, Y)

actorTarget.setParams(actor.getParams())
criticTarget.setParams(critic.getParams())

replayBuffer = rlp.ReplayBuffer(bufferSize)

# DDPG algorithm

# record actions and rewards from learning
actorAMat = np.zeros((numExp,aDim))
aMat = np.zeros((numExp,aDim))
timeMat = np.zeros((numExp, 2)) # duration of sequence and number of pulses
rMat = np.zeros((numExp,))
# record when resets/updates happen
resetStateEps = []
updateEps = [] # TODO remove this
# and record parameter differences between networks and targets (episode #, actor, critic)
paramDistance = []

# record test results: episode, final pulse sequence (to terminal state), rewards at each episode
testResults = []
isTesting = False

numActions = 0

print(f"starting DDPG algorithm ({datetime.now()})")

for i in range(numExp):
    if i % 100 == 0:
        print(f"On episode {i} ({datetime.now()})")
    s = env.getState()
    # get action based on current state and some level of noise
    actorA = actor.predict(s)
    if not isTesting:
        aNoise = rlp.actionNoise(p)
        a = rlp.clipAction(actorA + aNoise)
    else:
        a = rlp.clipAction(actorA)
    
    # update noise parameter
    p = np.maximum(p + dp, .05)
    
    numActions += 1
    
    # evolve state based on action
    env.evolve(a, Hint)
    # get reward
    r = env.reward2()
    
    # get updated state, and whether it's a terminal state
    s1 = env.getState()
    d = env.isDone()
    replayBuffer.add(s,a,r,s1,d)
    
    # record episode data
    aMat[i,:] = a
    actorAMat[i,:] = actorA
    rMat[i] = r
    timeMat[i,:] = [env.t, numActions]
    
    if i % int(numExp/25) == 0:
        # calculate distance between parameters for actors/critics
        paramDistance.append((i, actor.paramDistance(actorTarget), \
                                 critic.paramDistance(criticTarget)))
    
    # if the state is terminal
    if d:
        if isTesting:
            print(f"Recording test results (episode {i})")
            # record results from the test and go back to learning
            testResults.append((i, s1, rMat[(i-numActions+1):(i+1)]))
            print(f"Max reward from test: {np.max(rMat[(i-numActions+1):(i+1)]):0.02f}")
            isTesting = not isTesting
        else:
            # check if it's time to test performance
            if len(testResults)*testEvery < i:
                isTesting = True
        
        # randomize dipolar coupling strengths for Hint
        Hdip, Hint = ss.getAllH(N, dim, coupling, delta)
        # reset environment
        env.reset()
        resetStateEps.append(i)
        numActions = 0
    
    # update networks
    if i > updateAfter and i % updateEvery == 0:
        updateEps.append(i)
        for update in range(numUpdates):
            batch = replayBuffer.getSampleBatch(batchSize)
            # train critic
            critic.trainStep(batch, actorTarget, criticTarget)
            # train actor
            actor.trainStep(batch, critic)
            # update target networks
            criticTarget.copyParams(critic, polyak)
            actorTarget.copyParams(actor, polyak)
            
print(f"finished DDPG algorithm ({datetime.now()})")

# results (save everything to files)

plt.hist(rMat, bins=20, color='black', label='rewards')
plt.title('Rewards histogram')
plt.legend()
plt.savefig("../data/" + prefix + "/rewards_hist.png")
plt.clf()

plt.plot(rMat, 'ok', label='rewards')
ymin, ymax = plt.ylim()
plt.title('Rewards for each episode')
plt.xlabel('Episode number')
plt.ylabel('Reward')
plt.legend()
plt.savefig("../data/" + prefix + "/rewards_episode.png")
plt.clf()

plt.plot(aMat[:,0], 'ok', label='phi', zorder=1)
plt.plot(actorAMat[:,0], '.b', label='phi (actor)', zorder=2)
plt.title('Phi action')
ymin, ymax = plt.ylim()
plt.xlabel('Episode number')
plt.ylabel('Phi action')
plt.legend()
plt.savefig("../data/" + prefix + "/action_phi.png")
plt.clf()

plt.plot(aMat[:,1], 'ok', label='rot')
plt.plot(actorAMat[:,1], '.b', label='rot (actor)', zorder=2)
plt.title('Rot action')
ymin, ymax = plt.ylim()
plt.xlabel('Episode number')
plt.ylabel('Rot action')
plt.legend()
plt.savefig("../data/" + prefix + "/action_rot.png")
plt.clf()

plt.plot(aMat[:,2], 'ok', label='time')
plt.plot(actorAMat[:,2], '.b', label='time (actor)', zorder=2)
plt.title('Time action')
ymin, ymax = plt.ylim()
plt.xlabel('Episode number')
plt.ylabel('Time action')
plt.legend()
plt.savefig("../data/" + prefix + "/action_time.png")
plt.clf()

plt.plot(timeMat[:,0], 'ok', label='time')
plt.title('Pulse sequence length (time)')
ymin, ymax = plt.ylim()
plt.xlabel('Episode number')
plt.ylabel('Pulse sequence length (s)')
plt.legend()
plt.savefig("../data/" + prefix + "/sequence_length.png")
plt.clf()

plt.plot(timeMat[:,1], 'ok', label='time')
plt.title('Pulse sequence length (number of pulses)')
ymin, ymax = plt.ylim()
plt.xlabel('Episode number')
plt.ylabel('Number of pulses')
plt.legend()
plt.savefig("../data/" + prefix + "/sequence_number.png")
plt.clf()

# calculate other benchmarks of run

rBuffer = np.array([_[2] for _  in replayBuffer.buffer])
indSorted = rBuffer.argsort()
for i in range(1,5):
    output.write(f"Highest rewards in buffer (#{i})\n")
    output.write(f"Index in buffer: {indSorted[-i]}\n")
    sequence = replayBuffer.buffer[indSorted[-i]][3] # sequence of actions
    output.write(rlp.formatAction(sequence) + "\n")
    # calculate mean fidelity from ensemble of dipolar couplings
    fidelities = np.zeros((10,))
    t = np.sum(rlp.getTimeFromAction(sequence))
    for i in range(10):
        Hdip, Hint = ss.getAllH(N, dim, coupling, delta)
        Uexp = rlp.getPropagatorFromAction(N, dim, sequence, Hint, X, Y)
        Utarget = ss.getPropagator(HWHH0, t)
        fidelities[i] = ss.fidelity(Utarget, Uexp)
    fMean = np.mean(fidelities)
    output.write(f"Mean fidelity: {fMean}\n")
    r = -1*np.log10(1+1e-12-fMean**(20e-6/t))
    output.write(f"Reward: {r:.03}\n")

output.write("="*20 + "\n\n")
output.write("Test results\n\n")

for result in testResults:
    output.write(f"Test result from episode {result[0]}\n\nChosen pulse sequence:\n")
    output.write(rlp.formatAction(result[1]) + "\n")
    output.write(f"Rewards from the pulse sequence:\n{result[2]}\n\n")

output.write("="*20 + "\n\n")
output.write("Parameter distances\n")
for i in paramDistance:
    output.write(f"episode {i[0]}:\tactor diff={i[1]:0.2},\tcritic diff={i[2]:0.2}\n")

# clean up everything

output.flush()
output.close()

print("finished running script!")