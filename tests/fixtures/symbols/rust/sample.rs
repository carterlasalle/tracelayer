pub struct Point {
    x: i32,
    y: i32,
}

pub enum Shape {
    Circle,
    Square,
}

pub trait Draw {
    fn draw(&self);
}

impl Draw for Point {
    fn draw(&self) {}
}

fn free() -> i32 {
    1
}
