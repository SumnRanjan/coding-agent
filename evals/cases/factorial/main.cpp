#include <iostream>

long long factorial(int n) {
    long long result = 1;
    for (int i = 1; i < n; i++) {
        result *= i;
    }
    return result;
}

int main() {
    std::cout << "5! = " << factorial(5) << std::endl;
    std::cout << "6! = " << factorial(6) << std::endl;
    return 0;
}
