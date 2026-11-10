//! The same acquisition stack with UDP CAN input for local development.
#[allow(dead_code)]
#[path = "main.rs"]
mod acquisition;
fn main() -> anyhow::Result<()> {
    acquisition::run(true)
}
