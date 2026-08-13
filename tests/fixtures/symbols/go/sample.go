package main

func Add(a, b int) int {
    return a + b
}

type User struct {
    Name string
}

type Speaker interface {
    Speak()
}

func (u User) FullName() string {
    return u.Name
}
