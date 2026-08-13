package com.example;

public class Greeter {
    public Greeter() {}

    public String greet(String name) {
        return name;
    }

    interface Inner {
        void go();
    }
}

public interface Service {
    void run();
}

public enum Mode {
    ON,
    OFF
}
