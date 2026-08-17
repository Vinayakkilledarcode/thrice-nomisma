import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        String s = sc.nextLine();
        int count=0;
        String[] words = s.split(" ");
        for(int i = words.length - 1;i >= 0;i--){
            System.out.print(words[i]+" "+"\n");
            count++;
        }
        String rev = new StringBuilder(s).reverse().toString();
        System.out.print(rev+"\n");
        System.out.println(count);
    }
}