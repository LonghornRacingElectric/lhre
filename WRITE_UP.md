# BobSim study for optimal anti-squat, anti-dive, anti-roll
## Problem statement
We need to set anti-dive, anti-squat, anti-roll targets for the team. We will do this by writing a study utilizing VehicleSim.

## Tradeoff
Anti changes how much of your forces get transmitted through the rigid control arms versus your springs. Higher anti means your aero changes orientation less, but it comes at the cost of other dynamics. Notably, your Fz variation will increase per tire and your platform stability will be affected (i.e. less body pitch and roll)

## Our approach
We will sweep over vehicle geometries, using the control arm hardpoints as our way to change our Instant Center (IC). I also think we should prescribe anti-roll through the ARB. 

For each vehicle model instance, we need to get a set of metrics out of the sim that tells us how our model behaves. This is up to your idea, but we probablycare about 1) Fz over time for each tyre 2) yaw pitch roll of sprung mass 3) Lateral/Longitudinal load transfer. **This list can and probably should change based on our first principles**

To gather these metrics, we will curate a set of tracks to run our model over. These shouldn't be flashy, they should be designed to give us things we care about. What these tracks are are up to you. Remember that FSAE vehicles are almost always in transient conditions, so construct the tracks accordingly.

## How to implement
1) Decide what metrics we care about
2) Curate the tracks, and run a sample vehicle model on them as a proof of concept
3) Establish the sweep, the correlation to anti{dive,squat,roll} percentage

## What to keep in mind
**Simplicity is better**. Keep the mechanism simple, but still best-guess. Stick to the sweep parameters that I approve. If it is wrong, we can iterate -- it is harder to iterate on something that is overly complex. 

**using the tracks is probably the right idea here**

**Choose metrics wisely**. I should be able to reasonably reconstruct how two cars with different anti percentages behaved on the track from the data you give me.

**Avoid one-off scripts**. Reduce bloat so that any additions you make to this study could reasonably be shipped as a study to be merged to main.

**Follow good first-principles design**. When unsure, **ask**
