# lhr_trackgen

`publish_cones` generates the synthetic track and publishes its cone markers
on `/lhr/track/cones`. The kinematic demo uses those markers as scene truth;
the LiDAR caster reads their positions to render point returns. Cone IDs and
generator centerlines must not enter LiDAR detection or planning.

`cone_geometry.py` holds nominal small and large cone dimensions shared by
the caster and future visual models. Body slopes, stripe positions, and base
thickness are idealized rather than measurements of the installed cones.
The current generator publishes the ordinary blue and yellow small cones;
large start/finish cones are not enabled by this integration stage.
