import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        long a = sc.nextLong();
        long b = sc.nextLong();
        
        long x = a;
        long y = b;
        while(y != 0){
            long temp = y;
            y = x % y;
            x = temp;
        }
        
        long gcd = x;
        long lcm = (a / gcd) * b;
        System.out.println("GCD : "+ gcd);
        System.out.println("LCM : "+lcm);
    }
}